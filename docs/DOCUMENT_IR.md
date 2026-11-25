# 稿件中间表示协议（DOCUMENT_IR）

- 协议版本：`document-ir/1.0`
- 实现模块：`review_platform/document_engine/parser.py`

## 1. 设计原则

1. **不可变与零清洗失真**：`Block.text` 必须忠实保留 Word 段落/表格单元格中可见 `<w:t>`、`<w:tab/>`（`\t`）、`<w:br/>`（`\n`）拼接后的原文，**严禁做空格压缩、全角转半角或符号替换**，确保 `start..end` 坐标可直接回写原生 Word 批注。
2. **文件指纹强绑定**：`document_id` 取上传 DOCX 原始字节的 SHA-256 十六进制摘要；任何针对旧版文稿的锚点不得用于新文件。
3. **安全解析边界**：
   - 最大文件大小：10 MiB；
   - 最大 ZIP 解压总字节数：30 MiB；最大条目数：1000；
   - 禁用 DTD / XML 外部实体（拒绝含 `<!DOCTYPE` 或 `<!ENTITY` 的 XML）；
   - 显式检测 Word 修订痕迹（`<w:ins>`、`<w:del>`、`<w:moveFrom>`、`<w:moveTo>`），若存在则拒绝导入并提示先定稿接收/拒绝修订，防止在未定稿文本上产生错位批注。

## 2. 数据结构

- `DocumentIR`:
  - `schema_version`: `"document-ir/1.0"`
  - `document_id`: `str` (SHA-256)
  - `filename`: `str`
  - `blocks`: `list[Block]`
  - `profile`: `ManuscriptProfile`
  - `limitations`: `list[str]`（如：脚注、页眉页脚、文本框、嵌入图片/公式不纳入自动可见文本定位）
- `Block`:
  - `id`: `str`（正文 `P0001`..，表格 `T01R001C01`..，若同坐标多段则附加段序号）
  - `kind`: `title` | `heading` | `paragraph` | `reference` | `table_cell` | `separator`
  - `section`: `front` | `title` | `abstract` | `keywords` | `introduction` | `methods` | `results` | `discussion` | `conclusion` | `references` | `body` | `table`
  - `text`: `str`（Unicode 码点字符串）
  - `ooxml_part`: `"word/document.xml"`
  - `xml_path`: `str`（用于回写定位的确定性 XPath/索引路径，如 `body/p[3]` 或 `body/tbl[1]/tr[2]/tc[1]/p[1]`）
  - `runs`: `list[RunSpan]`（记录段内每个直接 `<w:r>` 在 `Block.text` 中对应的 `[start, end)` 与 `run_index`）
  - `meta`: `dict[str, Any]`（如 `table_no`, `row`, `col`, `style`）

## 3. 锚点校验契约（`validate_anchor`）

给定 `DocumentIR` 与 `Anchor`：
1. 检查 `anchor.document_id == doc.document_id`；
2. 查找 `block = block_map.get(anchor.block_id)`；
3. 检查 `0 <= anchor.start < anchor.end <= len(block.text)`；
4. 检查 `block.text[anchor.start:anchor.end] == anchor.quote`。
任意一步不满足即判定锚点无效，禁止伪造命中。
