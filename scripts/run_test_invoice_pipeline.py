from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ap_context import load_procurement_context, missing_procurement_context  # noqa: E402
from app.invoice_decision import decide_invoice  # noqa: E402
from app.invoice_normalizer import normalize_invoice_parse  # noqa: E402
from app.invoice_overlay import render_invoice_parse_overlay_pdf  # noqa: E402
from app.invoice_parser import parse_invoice_pdf  # noqa: E402


DEFAULT_INPUT_DIR = Path("storage/test_pdfs")
DEFAULT_OUTPUT_DIR = Path("storage/test_invoice_pipeline")


def run_test_invoice_pipeline(
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
        "schema_version": 1,
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "total": len(pdf_paths),
        "decision_matches_expected": 0,
        "decision_mismatches": 0,
        "missing_expected": 0,
        "overlay_written": 0,
        "overlay_failed": 0,
        "files": [],
    }

    for pdf_path in pdf_paths:
        file_summary = _run_one_pdf(pdf_path=pdf_path, output_dir=output_dir, box_mode=box_mode)
        summary["files"].append(file_summary)
        if file_summary.get("overlay_pdf"):
            summary["overlay_written"] += 1
        elif file_summary.get("overlay_error"):
            summary["overlay_failed"] += 1

        expected = file_summary.get("expected_decision")
        actual = file_summary.get("decision")
        if not expected:
            summary["missing_expected"] += 1
        elif actual == expected:
            summary["decision_matches_expected"] += 1
        else:
            summary["decision_mismatches"] += 1

    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    return summary


def _run_one_pdf(*, pdf_path: Path, output_dir: Path, box_mode: str) -> dict[str, Any]:
    manifest_path = pdf_path.with_suffix(".manifest.json")
    parsed_path = output_dir / f"{pdf_path.stem}.parsed.json"
    overlay_path = output_dir / f"{pdf_path.stem}.overlay.pdf"
    normalized_path = output_dir / f"{pdf_path.stem}.normalized.json"
    decision_path = output_dir / f"{pdf_path.stem}.decision.json"
    audit_path = output_dir / f"{pdf_path.stem}.audit.json"

    content = pdf_path.read_bytes()
    parse_result = parse_invoice_pdf(content, source_id=str(pdf_path))
    parsed_path.write_text(
        json.dumps(parse_result, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )

    overlay_error = None
    try:
        overlay = render_invoice_parse_overlay_pdf(content, parse_result, box_mode=box_mode)
        overlay_path.write_bytes(overlay)
    except Exception as exc:
        overlay_error = str(exc)

    normalized_invoice = normalize_invoice_parse(parse_result)
    normalized_path.write_text(
        json.dumps(normalized_invoice, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )

    if manifest_path.exists():
        procurement_context = load_procurement_context(manifest_path, invoice=normalized_invoice)
    else:
        procurement_context = missing_procurement_context("No generated manifest was found beside this PDF.")

    decision = decide_invoice(normalized_invoice, procurement_context)
    expected = procurement_context.get("expected") if isinstance(procurement_context.get("expected"), dict) else {}
    expected_decision = expected.get("decision")
    decision_payload = {
        "schema_version": 1,
        "pdf": str(pdf_path),
        "manifest": str(manifest_path) if manifest_path.exists() else None,
        "decision": decision,
        "expected_decision": expected_decision,
        "matches_expected": decision.get("decision") == expected_decision if expected_decision else None,
    }
    decision_path.write_text(
        json.dumps(decision_payload, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )

    audit = {
        "schema_version": 1,
        "pdf": str(pdf_path),
        "manifest": str(manifest_path) if manifest_path.exists() else None,
        "parsed_json": str(parsed_path),
        "overlay_pdf": str(overlay_path) if overlay_error is None else None,
        "normalized_json": str(normalized_path),
        "decision_json": str(decision_path),
        "parse_result": parse_result,
        "normalized_invoice": normalized_invoice,
        "procurement_context": procurement_context,
        "decision": decision,
        "expected_decision": expected_decision,
        "matches_expected": decision.get("decision") == expected_decision if expected_decision else None,
    }
    audit_path.write_text(
        json.dumps(audit, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )

    return {
        "pdf": str(pdf_path),
        "manifest": str(manifest_path) if manifest_path.exists() else None,
        "parsed_json": str(parsed_path),
        "overlay_pdf": str(overlay_path) if overlay_error is None else None,
        "overlay_error": overlay_error,
        "normalized_json": str(normalized_path),
        "decision_json": str(decision_path),
        "audit_json": str(audit_path),
        "parse_status": parse_result.get("status"),
        "decision": decision.get("decision"),
        "expected_decision": expected_decision,
        "matches_expected": decision.get("decision") == expected_decision if expected_decision else None,
    }


def _pdf_paths(input_dir: Path) -> list[Path]:
    return sorted(path for path in input_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")


def _json_default(value: Any) -> str:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the synthetic invoice parser-to-decision pipeline.")
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--boxes", choices=("parsed", "words", "all"), default="parsed")
    args = parser.parse_args()

    summary = run_test_invoice_pipeline(
        input_dir=args.input_dir,
        output_dir=args.output_dir,
        limit=args.limit,
        box_mode=args.boxes,
    )
    print(
        f"Wrote {summary['total']} audit runs to {summary['output_dir']} "
        f"({summary['decision_matches_expected']} matched expected, "
        f"{summary['decision_mismatches']} mismatched, "
        f"{summary['missing_expected']} without expected decisions)."
    )


if __name__ == "__main__":
    main()
