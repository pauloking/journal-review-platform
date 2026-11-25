"""Knowledge Engine：结构化规范条款、规范文档与期刊配置包管理。"""

from .doc_parser import parse_rule_document_bytes
from .store import KnowledgeEngine

__all__ = ["KnowledgeEngine", "parse_rule_document_bytes"]
