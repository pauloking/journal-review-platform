"""Document Engine：DOCX 安全解析、Document IR 与原生锚点校验。"""

from .parser import DocumentParseError, parse_docx_bytes, validate_anchor

__all__ = ["DocumentParseError", "parse_docx_bytes", "validate_anchor"]
