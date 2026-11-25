"""命令行审校入口：python -m review_platform <manuscript.docx> [--journal ...] [--output ...]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from review_platform.document_engine import DocumentParseError
from review_platform.editorial_engine import export_json, export_markdown
from review_platform.pipeline import PlatformContext


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="中文科技期刊智能审校能力平台 CLI")
    parser.add_argument("docx_path", help="待审校 DOCX 文件路径")
    parser.add_argument("--journal", default="general-medical-cn", help="期刊配置 ID")
    parser.add_argument("--format", choices=["json", "markdown"], default="json", help="输出格式")
    parser.add_argument("--output", help="输出文件路径（不指定则输出到 stdout）")
    args = parser.parse_args(argv)

    path = Path(args.docx_path)
    if not path.exists():
        sys.stderr.write(f"文件不存在: {path}\n")
        return 1

    ctx = PlatformContext()
    try:
        run = ctx.run_review(path.read_bytes(), filename=path.name, journal_id=args.journal)
    except (DocumentParseError, KeyError) as exc:
        sys.stderr.write(f"审校执行失败: {exc}\n")
        return 1

    output_text = export_json(run) if args.format == "json" else export_markdown(run)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(output_text, encoding="utf-8")
    else:
        sys.stdout.write(output_text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
