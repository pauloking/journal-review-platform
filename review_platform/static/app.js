let currentRun = null;
let lastUploadedFile = null;
let selectedAnchor = null;
let registryData = null;
let selectedDocId = null;

const SCOPE_LABELS = {
  national: '国家标准',
  professional: '行业规范',
  journal: '期刊体例',
  experience: '编辑经验',
};

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderInlineMd(line) {
  let out = esc(line);
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>');
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  out = out.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  return out;
}

function renderMarkdown(md) {
  const lines = String(md || '').split(/\r?\n/);
  const html = [];
  let inUl = false;
  let inOl = false;

  const closeLists = () => {
    if (inUl) { html.push('</ul>'); inUl = false; }
    if (inOl) { html.push('</ol>'); inOl = false; }
  };

  for (const raw of lines) {
    const line = raw.trimEnd();
    if (!line.trim()) {
      closeLists();
      continue;
    }
    if (line.startsWith('### ')) {
      closeLists();
      html.push(`<h3>${renderInlineMd(line.slice(4))}</h3>`);
    } else if (line.startsWith('## ')) {
      closeLists();
      html.push(`<h2>${renderInlineMd(line.slice(3))}</h2>`);
    } else if (line.startsWith('# ')) {
      closeLists();
      html.push(`<h1>${renderInlineMd(line.slice(2))}</h1>`);
    } else if (line.startsWith('> ')) {
      closeLists();
      html.push(`<blockquote>${renderInlineMd(line.slice(2))}</blockquote>`);
    } else if (/^[-*]\s+/.test(line.trim())) {
      if (inOl) { html.push('</ol>'); inOl = false; }
      if (!inUl) { html.push('<ul>'); inUl = true; }
      html.push(`<li>${renderInlineMd(line.trim().replace(/^[-*]\s+/, ''))}</li>`);
    } else if (/^\d+\.\s+/.test(line.trim())) {
      if (inUl) { html.push('</ul>'); inUl = false; }
      if (!inOl) { html.push('<ol>'); inOl = true; }
      html.push(`<li>${renderInlineMd(line.trim().replace(/^\d+\.\s+/, ''))}</li>`);
    } else {
      closeLists();
      html.push(`<p>${renderInlineMd(line)}</p>`);
    }
  }
  closeLists();
  return html.join('');
}

function showBanner(msg, isError = false) {
  const el = document.getElementById('status-banner');
  el.textContent = msg;
  el.className = isError ? 'banner error' : 'banner';
}

async function refreshRegistry() {
  const regRes = await fetch('/registry');
  const reg = await regRes.json();
  registryData = reg;
  document.getElementById('version-badge').textContent = `V${reg.version} · ${reg.rule_snapshot_hash}`;

  const sel = document.getElementById('journal-select');
  const prevVal = sel.value;
  sel.innerHTML = reg.journals
    .map((j) => `<option value="${esc(j.id)}">${esc(j.name)} (${esc(j.id)})</option>`)
    .join('');
  if (prevVal) sel.value = prevVal;

  renderDocumentLibrary();
}

async function init() {
  document.querySelectorAll('.tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
      document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
      btn.classList.add('active');
      document.getElementById('tab-' + btn.dataset.tab).classList.add('active');
      if (btn.dataset.tab === 'evaluation') loadEvaluation();
    });
  });

  await refreshRegistry();

  document.getElementById('run-btn').addEventListener('click', runReview);
  document.getElementById('load-run-btn').addEventListener('click', refreshCurrentRun);
  document.getElementById('filter-decision').addEventListener('change', renderFindings);
  document.getElementById('export-json-btn').addEventListener('click', () => downloadExport('json'));
  document.getElementById('export-md-btn').addEventListener('click', () => downloadExport('markdown'));
  document.getElementById('export-summary-btn').addEventListener('click', () => downloadExport('summary_docx'));
  document.getElementById('export-word-btn').addEventListener('click', exportWordComments);

  document.getElementById('doc-search-input').addEventListener('input', renderDocumentList);
  document.getElementById('new-doc-btn').addEventListener('click', () => openDocumentEditor(null));

  const hashRun = new URLSearchParams(location.search).get('run_id');
  if (hashRun) {
    await loadRunById(hashRun);
  }
}

async function runReview() {
  const input = document.getElementById('docx-input');
  const file = input.files?.[0];
  if (!file) {
    showBanner('请先选择一个 .docx 稿件文件', true);
    return;
  }
  lastUploadedFile = file;
  const journal = document.getElementById('journal-select').value;
  const fd = new FormData();
  fd.append('file', file);

  showBanner('正在解析 Document IR、生成 Router DAG 并执行确定性规则...');
  const res = await fetch(`/reviews?journal=${encodeURIComponent(journal)}`, { method: 'POST', body: fd });
  const data = await res.json();
  if (!res.ok) {
    showBanner(data.detail || '审校执行失败', true);
    return;
  }
  currentRun = data;
  selectedAnchor = null;
  history.replaceState(null, '', `?run_id=${encodeURIComponent(data.run_id)}`);
  renderRun();
  showBanner(`审校完成：共生成 ${data.findings.length} 条可验证意见`);
}

async function loadRunById(runId) {
  const res = await fetch(`/reviews/${encodeURIComponent(runId)}`);
  if (!res.ok) {
    showBanner('未能加载指定 run_id', true);
    return;
  }
  currentRun = await res.json();
  renderRun();
}

async function refreshCurrentRun() {
  if (!currentRun) return;
  await loadRunById(currentRun.run_id);
  showBanner('已刷新最新裁决状态');
}

function latestDecisionsMap() {
  const map = {};
  if (!currentRun) return map;
  for (const d of currentRun.decisions || []) {
    if (!map[d.finding_id] || d.revision > map[d.finding_id].revision) {
      map[d.finding_id] = d;
    }
  }
  return map;
}

function renderRun() {
  if (!currentRun) return;
  document.getElementById('run-overview').classList.remove('hidden');
  document.getElementById('m-run-id').textContent = currentRun.run_id;
  const p = currentRun.document.profile;
  document.getElementById('m-doc-stats').textContent = `${p.total_blocks} 块 / ${p.char_count} 字`;
  document.getElementById('m-task-stats').textContent = `${currentRun.plan.tasks.length} 任务`;
  document.getElementById('m-finding-stats').textContent = `${currentRun.findings.length} 条`;
  document.getElementById('doc-profile-tag').textContent = `结构状态: ${p.structure_status}`;

  renderDocumentBlocks();
  renderFindings();
  renderPlanTable();
}

function renderDocumentBlocks() {
  const wrap = document.getElementById('doc-blocks');
  wrap.innerHTML = currentRun.document.blocks
    .map((b) => {
      const isTarget = selectedAnchor && selectedAnchor.block_id === b.id;
      let htmlText = esc(b.text);
      if (isTarget) {
        const s = selectedAnchor.start;
        const e = selectedAnchor.end;
        const before = esc(b.text.slice(0, s));
        const mid = esc(b.text.slice(s, e));
        const after = esc(b.text.slice(e));
        htmlText = `${before}<mark class="quote-hl">${mid}</mark>${after}`;
      }
      return `<div class="doc-block ${isTarget ? 'active-block' : ''}" id="block-${esc(b.id)}">
        <div class="block-meta">[${esc(b.id)}] ${esc(b.section_cn)} (${esc(b.kind)})</div>
        <div>${htmlText}</div>
      </div>`;
    })
    .join('');

  if (selectedAnchor) {
    const targetEl = document.getElementById('block-' + selectedAnchor.block_id);
    if (targetEl) targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
}

function renderFindings() {
  const wrap = document.getElementById('findings-list');
  const filter = document.getElementById('filter-decision').value;
  const latest = latestDecisionsMap();
  const actionLabels = { accept: '已采纳', reject: '已驳回', defer: '暂缓' };

  const items = currentRun.findings.filter((f) => {
    const dec = latest[f.id];
    const st = dec ? dec.action : 'pending';
    return filter === 'all' || filter === st;
  });

  if (!items.length) {
    wrap.innerHTML = '<p class="hint">当前筛选条件下无审校意见。</p>';
    return;
  }

  wrap.innerHTML = items
    .map((f) => {
      const dec = latest[f.id];
      const rev = dec ? dec.revision : 0;
      const decText = dec ? `${actionLabels[dec.action]} (v${dec.revision})` : '待裁决';
      const decCls = dec ? `dec-${dec.action}` : '';
      return `<div class="finding-card">
        <div class="finding-head">
          <span class="sev-${esc(f.severity)}">[${esc(f.severity.toUpperCase())}] ${esc(f.rule_id)} · ${esc(f.title)}</span>
          <span class="dec-pill ${decCls}">${esc(decText)}</span>
        </div>
        <div class="hint">定位：<code>${esc(f.anchor.block_id)}</code> [${f.anchor.start}:${f.anchor.end}] · 原文「<b>${esc(f.anchor.quote)}</b>」</div>
        <div><b>问题：</b>${esc(f.message)}</div>
        <div><b>建议：</b>${esc(f.suggestion)}</div>
        <div class="hint">依据：<code>${esc(f.basis_id)}</code>（${esc(f.basis_source)}）</div>
        <div class="finding-actions">
          <button class="btn small" onclick="locateAnchor('${esc(f.id)}')">定位原文</button>
          <input class="reason-input" id="reason-${esc(f.id)}" placeholder="填写裁决理由（必填）" value="${dec ? esc(dec.reason) : ''}" />
          <button class="btn small" onclick="submitDecision('${esc(f.id)}', 'accept', ${rev})">采纳</button>
          <button class="btn small" onclick="submitDecision('${esc(f.id)}', 'defer', ${rev})">暂缓</button>
          <button class="btn small" onclick="submitDecision('${esc(f.id)}', 'reject', ${rev})">驳回</button>
        </div>
      </div>`;
    })
    .join('');
}

window.locateAnchor = function (findingId) {
  const f = currentRun.findings.find((x) => x.id === findingId);
  if (!f) return;
  selectedAnchor = f.anchor;
  renderDocumentBlocks();
};

window.submitDecision = async function (findingId, action, expectedRev) {
  const editor = document.getElementById('editor-name').value.trim() || '责编';
  const reasonEl = document.getElementById('reason-' + findingId);
  const reason = (reasonEl?.value || '').trim() || (action === 'accept' ? '确认问题有效，采纳修改建议' : '经编辑复核后处理');

  const res = await fetch(`/reviews/${encodeURIComponent(currentRun.run_id)}/decisions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      finding_id: findingId,
      action,
      editor,
      reason,
      expected_revision: expectedRev,
    }),
  });
  const data = await res.json();
  if (!res.ok) {
    showBanner(data.detail || '提交裁决失败', true);
    return;
  }
  await refreshCurrentRun();
};

function renderPlanTable() {
  const rows = currentRun.plan.tasks
    .map(
      (t) => `<tr>
      <td><code>${esc(t.node_id)}</code></td>
      <td>${esc(t.category)}</td>
      <td><code>${esc(t.status)}</code></td>
      <td>${t.checked_count}</td>
      <td>${t.excluded_count}</td>
      <td>${t.finding_count}</td>
      <td>${esc(t.reason)}</td>
    </tr>`
    )
    .join('');
  document.getElementById('plan-table-wrap').innerHTML = `<table>
    <tr><th>节点</th><th>类别</th><th>状态</th><th>检查数</th><th>排除数</th><th>命中数</th><th>说明</th></tr>
    ${rows}
  </table>`;
}

function downloadExport(fmt) {
  if (!currentRun) return;
  window.location.href = `/reviews/${encodeURIComponent(currentRun.run_id)}/export?format=${encodeURIComponent(fmt)}`;
}

async function exportWordComments() {
  if (!currentRun) return;
  const file = document.getElementById('docx-input').files?.[0] || lastUploadedFile;
  if (!file) {
    showBanner('导出原生批注需要重新选择与本审校一致的原始 DOCX 文件（用于 SHA-256 校验）', true);
    return;
  }
  const fd = new FormData();
  fd.append('file', file);
  const res = await fetch(`/reviews/${encodeURIComponent(currentRun.run_id)}/export/docx`, {
    method: 'POST',
    body: fd,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    showBanner(err.detail || '导出批注 DOCX 失败', true);
    return;
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `原生批注_${currentRun.run_id}.docx`;
  a.click();
  URL.revokeObjectURL(url);
  showBanner('已导出带原生 Word 批注的 DOCX 副本');
}

function renderDocumentLibrary() {
  const docs = registryData?.documents || [];
  if (!selectedDocId && docs.length) {
    selectedDocId = docs[0].doc_id;
  }
  renderDocumentList();
  if (selectedDocId) {
    const doc = docs.find((d) => d.doc_id === selectedDocId) || docs[0];
    if (doc) renderDocumentViewer(doc);
  }
}

function renderDocumentList() {
  const docs = registryData?.documents || [];
  const q = (document.getElementById('doc-search-input')?.value || '').trim().toLowerCase();
  const filtered = docs.filter(
    (d) =>
      !q ||
      d.title.toLowerCase().includes(q) ||
      d.source.toLowerCase().includes(q) ||
      d.summary.toLowerCase().includes(q)
  );

  const groups = { national: [], professional: [], journal: [], experience: [] };
  for (const d of filtered) {
    (groups[d.scope] || groups.journal).push(d);
  }

  const parts = [];
  for (const scope of ['national', 'professional', 'journal', 'experience']) {
    const list = groups[scope];
    if (!list.length) continue;
    parts.push(`<div class="doc-group-title">${esc(SCOPE_LABELS[scope] || scope)}</div>`);
    for (const d of list) {
      const activeCls = d.doc_id === selectedDocId ? 'active' : '';
      const ruleCount = (d.maintainable_rules || []).length;
      parts.push(`<button class="doc-nav-item ${activeCls}" onclick="selectKnowledgeDoc('${esc(d.doc_id)}')">
        <div class="doc-nav-title">${esc(d.title)}</div>
        <div class="doc-nav-meta">
          <span>${esc(d.edition)} · ${d.is_builtin ? '内置规范' : '本刊定制'}</span>
          <span>${ruleCount} 条规则</span>
        </div>
      </button>`);
    }
  }
  document.getElementById('doc-list').innerHTML =
    parts.join('') || '<p class="hint">没有匹配的规范文档。</p>';
}

window.selectKnowledgeDoc = function (docId) {
  selectedDocId = docId;
  renderDocumentList();
  const doc = (registryData?.documents || []).find((d) => d.doc_id === docId);
  if (doc) renderDocumentViewer(doc);
};

function renderDocumentViewer(doc) {
  const viewer = document.getElementById('doc-viewer');
  const editor = document.getElementById('doc-editor');
  editor.classList.add('hidden');
  viewer.classList.remove('hidden');

  const rules = doc.maintainable_rules || [];
  const ruleRows = rules
    .map((r) => {
      const modeLabel = r.nonstandard ? '自动检查' : '人工核对';
      const exBad = r.example ? `<span class="ex-bad">错例：${esc(r.example)}</span>` : '—';
      const exOk = r.counter_example ? `<span class="ex-ok">正写/豁免：${esc(r.counter_example)}</span>` : '—';
      const exc = (r.exception_after || []).length
        ? `<div class="hint">例外后缀：${esc(r.exception_after.join(' / '))}</div>`
        : '';
      return `<tr>
        <td><b>${esc(r.description || r.rule_id)}</b><div class="hint">${esc(modeLabel)} · 级别：${esc(r.severity)}</div></td>
        <td>${r.nonstandard ? `<code>${esc(r.nonstandard)}</code>` : '（人工核对项）'}${exc}</td>
        <td>${esc(r.standard || '—')}</td>
        <td>${exBad}<br/>${exOk}</td>
      </tr>`;
    })
    .join('');

  viewer.innerHTML = `
    <div class="doc-header">
      <div>
        <h2>${esc(doc.title)}</h2>
        <div class="hint">
          <span class="scope-badge">${esc(SCOPE_LABELS[doc.scope] || doc.scope)}</span>
          依据编号：<code>${esc(doc.basis_id)}</code> ｜ 版本：<code>v${esc(doc.version)}</code> ｜ 来源：${esc(doc.source)}
        </div>
      </div>
      <div class="actions">
        <a class="btn small" href="/knowledge/documents/${encodeURIComponent(doc.doc_id)}/export">导出 .md</a>
        <button class="btn small primary" onclick="openDocumentEditor('${esc(doc.doc_id)}')">编辑与维护此文档</button>
      </div>
    </div>
    <div class="md-body">${renderMarkdown(doc.body_markdown)}</div>
    <div class="doc-rules-section">
      <h3>本文档关联的可执行核查对照（共 ${rules.length} 项）</h3>
      <table>
        <tr><th>核查说明</th><th>匹配写法 / 触发条件</th><th>规范建议</th><th>错例与正确对照</th></tr>
        ${ruleRows || '<tr><td colspan="4">暂无结构化条目</td></tr>'}
      </table>
      <details style="margin-top:10px;">
        <summary class="hint" style="cursor:pointer;">查看底层规则编号与注册快照（技术细节）</summary>
        <p class="hint">规则 ID：${(doc.rule_ids || []).map((id) => `<code>${esc(id)}</code>`).join(' ')}</p>
      </details>
    </div>
  `;
}

let editingRulesBuffer = [];
let editingDocId = null;

window.openDocumentEditor = function (docId) {
  const doc = docId ? (registryData?.documents || []).find((d) => d.doc_id === docId) : null;
  editingDocId = doc ? doc.doc_id : null;
  editingRulesBuffer = doc
    ? JSON.parse(JSON.stringify(doc.maintainable_rules || []))
    : [
        {
          rule_id: 'CUST-TERM-01',
          nonstandard: '',
          standard: '',
          severity: 'warning',
          is_regex: false,
          exception_after: [],
          example: '',
          counter_example: '',
          description: '',
        },
      ];

  const viewer = document.getElementById('doc-viewer');
  const editor = document.getElementById('doc-editor');
  viewer.classList.add('hidden');
  editor.classList.remove('hidden');

  const canEditRules = !doc || doc.supports_rule_editing;

  editor.innerHTML = `
    <div class="doc-header">
      <div>
        <h2>${doc ? `维护文档：${esc(doc.title)}` : '新建规范文档'}</h2>
        <p class="hint">保存前系统会自动校验每条规则的正反例（必须命中错例且不误报正例），通过后方才激活生效。</p>
      </div>
      <div class="actions">
        <button class="btn small" onclick="cancelDocumentEditor()">取消返回</button>
        <button class="btn small primary" onclick="saveKnowledgeDocument()">验证正反例并保存</button>
      </div>
    </div>
    <div id="doc-editor-error" class="banner error hidden"></div>
    <div class="editor-grid">
      <div class="editor-field"><label>文档标题</label><input id="ed-title" value="${esc(doc?.title || '')}" placeholder="例如：《心血管专科术语补充规范》" /></div>
      <div class="editor-field"><label>依据来源</label><input id="ed-source" value="${esc(doc?.source || '')}" placeholder="例如：本刊编辑部规范 v2026" /></div>
      <div class="editor-field"><label>版本年份</label><input id="ed-edition" value="${esc(doc?.edition || '2026')}" /></div>
      <div class="editor-field"><label>规范层级</label>
        <select id="ed-scope">
          <option value="national" ${doc?.scope === 'national' ? 'selected' : ''}>国家标准</option>
          <option value="professional" ${doc?.scope === 'professional' ? 'selected' : ''}>行业规范</option>
          <option value="journal" ${!doc || doc.scope === 'journal' ? 'selected' : ''}>期刊体例</option>
          <option value="experience" ${doc?.scope === 'experience' ? 'selected' : ''}>编辑经验</option>
        </select>
      </div>
    </div>
    <div class="editor-field"><label>核心摘要</label><input id="ed-summary" value="${esc(doc?.summary || '')}" placeholder="简要描述本规范文档的适用目标" /></div>
    <div class="editor-field"><label>规范文档正文（Markdown）</label><textarea id="ed-md" rows="10">${esc(doc?.body_markdown || '# 新建规范文档\n\n## 1. 适用范围\n\n## 2. 编校要求\n')}</textarea></div>
    <div class="doc-rules-section">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <h3 style="margin:0;">可执行规则条目${canEditRules ? '' : '（数学/结构 Python 内置验证器，此处仅维护文档说明）'}</h3>
        ${canEditRules ? '<button class="btn small" onclick="addEditableRuleRow()">+ 添加规则条目</button>' : ''}
      </div>
      <div id="editable-rules-list"></div>
    </div>
  `;
  renderEditableRuleRows(canEditRules);
};

function renderEditableRuleRows(canEditRules) {
  const wrap = document.getElementById('editable-rules-list');
  if (!canEditRules) {
    wrap.innerHTML = '<p class="hint">该文档关联底层 Python 数学复算/题注序列引擎，为防逻辑损坏仅支持维护文档正文。</p>';
    return;
  }
  wrap.innerHTML = editingRulesBuffer
    .map(
      (r, idx) => `<div class="rule-edit-row">
      <div class="rule-edit-cols">
        <input placeholder="规则ID (如 TERM-NEW)" value="${esc(r.rule_id)}" onchange="updateRuleField(${idx}, 'rule_id', this.value)" />
        <input placeholder="非规范写法 (留空=人工清单)" value="${esc(r.nonstandard)}" onchange="updateRuleField(${idx}, 'nonstandard', this.value)" />
        <input placeholder="建议规范写法" value="${esc(r.standard)}" onchange="updateRuleField(${idx}, 'standard', this.value)" />
        <select onchange="updateRuleField(${idx}, 'severity', this.value)">
          <option value="error" ${r.severity === 'error' ? 'selected' : ''}>严重 (error)</option>
          <option value="warning" ${r.severity === 'warning' ? 'selected' : ''}>警告 (warning)</option>
          <option value="info" ${r.severity === 'info' ? 'selected' : ''}>提示 (info)</option>
        </select>
      </div>
      <div class="rule-edit-cols">
        <input placeholder="正例：应命中的错误句 (自动规则必填)" value="${esc(r.example)}" onchange="updateRuleField(${idx}, 'example', this.value)" />
        <input placeholder="反例：不应误报的正确句" value="${esc(r.counter_example)}" onchange="updateRuleField(${idx}, 'counter_example', this.value)" />
        <input placeholder="例外后缀 (逗号分隔)" value="${esc((r.exception_after || []).join(','))}" onchange="updateRuleField(${idx}, 'exception_after', this.value.split(',').map(s=>s.trim()).filter(Boolean))" />
        <input placeholder="人类可读提示说明" value="${esc(r.description)}" onchange="updateRuleField(${idx}, 'description', this.value)" />
      </div>
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;">
        <label><input type="checkbox" ${r.is_regex ? 'checked' : ''} onchange="updateRuleField(${idx}, 'is_regex', this.checked)" /> 启用正则表达式模式</label>
        <button class="btn small" onclick="removeEditableRuleRow(${idx})">删除此条</button>
      </div>
    </div>`
    )
    .join('');
}

window.updateRuleField = function (idx, field, val) {
  editingRulesBuffer[idx][field] = val;
};

window.addEditableRuleRow = function () {
  editingRulesBuffer.push({
    rule_id: `CUST-${editingRulesBuffer.length + 1}`,
    nonstandard: '',
    standard: '',
    severity: 'warning',
    is_regex: false,
    exception_after: [],
    example: '',
    counter_example: '',
    description: '',
  });
  renderEditableRuleRows(true);
};

window.removeEditableRuleRow = function (idx) {
  editingRulesBuffer.splice(idx, 1);
  renderEditableRuleRows(true);
};

window.cancelDocumentEditor = function () {
  const doc = (registryData?.documents || []).find((d) => d.doc_id === selectedDocId);
  if (doc) renderDocumentViewer(doc);
};

window.saveKnowledgeDocument = async function () {
  const errBox = document.getElementById('doc-editor-error');
  errBox.classList.add('hidden');
  const payload = {
    doc_id: editingDocId,
    title: document.getElementById('ed-title').value.trim(),
    source: document.getElementById('ed-source').value.trim(),
    edition: document.getElementById('ed-edition').value.trim() || '2026',
    scope: document.getElementById('ed-scope').value,
    summary: document.getElementById('ed-summary').value.trim(),
    body_markdown: document.getElementById('ed-md').value,
    rules: editingRulesBuffer,
  };
  const url = editingDocId
    ? `/knowledge/documents/${encodeURIComponent(editingDocId)}`
    : '/knowledge/documents';
  const method = editingDocId ? 'PUT' : 'POST';

  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok) {
    errBox.textContent = data.detail || '验证或保存失败';
    errBox.classList.remove('hidden');
    return;
  }
  selectedDocId = data.doc_id;
  await refreshRegistry();
};

async function loadEvaluation() {
  const res = await fetch('/evaluation/feedback');
  const data = await res.json();
  const s = data.summary;
  const rows = (data.recent || [])
    .map(
      (r) => `<tr>
      <td>${esc(r.created_at)}</td>
      <td><code>${esc(r.rule_id)}</code></td>
      <td>${esc(r.action)}</td>
      <td>${esc(r.editor)}</td>
      <td>${esc(r.reason)}</td>
    </tr>`
    )
    .join('');
  document.getElementById('eval-content').innerHTML = `
    <p>总反馈条数：<b>${s.total_feedback}</b> ｜ 动作分布：<code>${esc(JSON.stringify(s.actions))}</code> ｜ 大类分布：<code>${esc(JSON.stringify(s.by_group))}</code></p>
    <table>
      <tr><th>时间</th><th>规则</th><th>动作</th><th>编辑</th><th>理由</th></tr>
      ${rows || '<tr><td colspan="5">暂无反馈事件</td></tr>'}
    </table>
  `;
}

init();
