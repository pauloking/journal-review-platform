let currentRun = null;
let lastUploadedFile = null;
let selectedAnchor = null;

function esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function showBanner(msg, isError = false) {
  const el = document.getElementById('status-banner');
  el.textContent = msg;
  el.className = isError ? 'banner error' : 'banner';
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

  const regRes = await fetch('/registry');
  const reg = await regRes.json();
  document.getElementById('version-badge').textContent = `V${reg.version} · ${reg.rule_snapshot_hash}`;

  const sel = document.getElementById('journal-select');
  sel.innerHTML = reg.journals
    .map((j) => `<option value="${esc(j.id)}">${esc(j.name)} (${esc(j.id)})</option>`)
    .join('');

  renderRegistry(reg);

  document.getElementById('run-btn').addEventListener('click', runReview);
  document.getElementById('load-run-btn').addEventListener('click', refreshCurrentRun);
  document.getElementById('filter-decision').addEventListener('change', renderFindings);
  document.getElementById('export-json-btn').addEventListener('click', () => downloadExport('json'));
  document.getElementById('export-md-btn').addEventListener('click', () => downloadExport('markdown'));
  document.getElementById('export-summary-btn').addEventListener('click', () => downloadExport('summary_docx'));
  document.getElementById('export-word-btn').addEventListener('click', exportWordComments);

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

function renderRegistry(reg) {
  const clauseMap = {};
  for (const c of reg.clauses) clauseMap[c.id] = c;
  const rows = reg.rules
    .map((r) => {
      const c = clauseMap[r.basis_id];
      return `<tr>
        <td><code>${esc(r.id)}</code> v${esc(r.version)}</td>
        <td>${esc(r.scope)}</td>
        <td>${esc(r.category)}</td>
        <td>${r.auto ? '自动' : '人工清单'}</td>
        <td><code>${esc(r.basis_id)}</code><br/><span class="hint">${esc(c?.source || '')}</span></td>
        <td>${esc(r.example || '-')}</td>
      </tr>`;
    })
    .join('');
  document.getElementById('registry-content').innerHTML = `<table>
    <tr><th>规则 ID / 版本</th><th>层级</th><th>类别</th><th>模式</th><th>知识依据</th><th>正例</th></tr>
    ${rows}
  </table>`;
}

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
