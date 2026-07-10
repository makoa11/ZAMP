from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.invoice_parser import parse_invoice_pdf
from app.invoice_overlay import render_invoice_parse_overlay_pdf


DEFAULT_INPUT_DIR = Path("storage/test_pdfs")
DEFAULT_OUTPUT_DIR = Path("storage/parsed")


def _pdf_paths(input_dir: Path) -> list[Path]:
    return sorted(
        path
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() == ".pdf"
    )


def parse_test_pdfs(
    *,
    input_dir: Path = DEFAULT_INPUT_DIR,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    limit: int | None = None,
    box_mode: str = "parsed",
) -> dict[str, Any]:
    pdf_paths = _pdf_paths(input_dir)
    if limit is not None:
        pdf_paths = pdf_paths[:limit]

    output_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "total": len(pdf_paths),
        "parsed": 0,
        "failed": 0,
        "overlay_written": 0,
        "overlay_failed": 0,
        "box_mode": box_mode,
        "warnings": [],
        "files": [],
    }

    for pdf_path in pdf_paths:
        json_path = output_dir / f"{pdf_path.stem}.parsed.json"
        overlay_path = output_dir / f"{pdf_path.stem}.parsed.pdf"
        try:
            content = pdf_path.read_bytes()
            result = parse_invoice_pdf(content, source_id=str(pdf_path))
        except Exception as exc:
            content = b""
            result = {
                "status": "failed",
                "source_id": str(pdf_path),
                "warnings": [f"Parse script failed: {exc}"],
                "fields": {"line_items": []},
            }

        overlay_written = False
        if content:
            try:
                overlay = render_invoice_parse_overlay_pdf(content, result, box_mode=box_mode)
                overlay_path.write_bytes(overlay)
                overlay_written = True
                summary["overlay_written"] += 1
            except Exception as exc:
                summary["overlay_failed"] += 1
                result.setdefault("warnings", []).append(f"Overlay render failed: {exc}")

        json_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

        status = str(result.get("status") or "unknown")
        warnings = result.get("warnings") if isinstance(result.get("warnings"), list) else []
        line_items = (result.get("fields") or {}).get("line_items") if isinstance(result.get("fields"), dict) else []
        file_summary = {
            "pdf": str(pdf_path),
            "json": str(json_path),
            "overlay_pdf": str(overlay_path) if overlay_written else None,
            "status": status,
            "line_item_count": len(line_items) if isinstance(line_items, list) else 0,
            "warning_count": len(warnings),
        }
        summary["files"].append(file_summary)
        if status == "parsed":
            summary["parsed"] += 1
        else:
            summary["failed"] += 1
        for warning in warnings:
            summary["warnings"].append({"pdf": str(pdf_path), "warning": str(warning)})

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse generated invoice sample PDFs to JSON and overlay PDFs.")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Directory containing PDF files to parse.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory where parsed JSON files are written.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Optional max number of PDFs to parse.")
    parser.add_argument(
        "--boxes",
        choices=("parsed", "words", "all"),
        default="parsed",
        help="Overlay boxes to plot in the generated PDF. Defaults to parsed parser evidence.",
    )
    args = parser.parse_args()

    summary = parse_test_pdfs(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        limit=args.limit,
        box_mode=args.boxes,
    )
    print(
        f"Wrote {summary['total']} parsed JSON files and {summary['overlay_written']} overlay PDFs "
        f"to {summary['output_dir']} "
        f"({summary['parsed']} parsed, {summary['failed']} failed, "
        f"{summary['overlay_failed']} overlay failures, {len(summary['warnings'])} warnings)."
    )


if __name__ == "__main__":
    main()
