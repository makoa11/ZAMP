from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.ap_context import load_procurement_context, missing_procurement_context  # noqa: E402
from app.invoice_decision import decide_invoice  # noqa: E402
from app.invoice_normalizer import normalize_invoice_parse  # noqa: E402
from app.invoice_parser import parse_invoice_pdf  # noqa: E402


def run_invoice_decision(
    *,
    pdf_path: Path,
    manifest_path: Path | None = None,
    output_path: Path | None = None,
) -> dict[str, Any]:
    parse_result = parse_invoice_pdf(pdf_path.read_bytes(), source_id=str(pdf_path))
    invoice = normalize_invoice_parse(parse_result)
    if manifest_path is None:
        context = missing_procurement_context("No procurement manifest was provided.")
    else:
        context = load_procurement_context(manifest_path, invoice=invoice)
    decision = decide_invoice(invoice, context)
    result = {
        "schema_version": 1,
        "pdf": str(pdf_path),
        "manifest": str(manifest_path) if manifest_path else None,
        "invoice": invoice,
        "procurement_context": context,
        "decision": decision,
    }

    destination = output_path or pdf_path.with_suffix(".decision.json")
    destination.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    result["output_path"] = str(destination)
    return result


def _print_summary(result: dict[str, Any]) -> None:
    decision = result["decision"]
    print(f"Decision: {decision['decision']} ({decision['confidence']} confidence)")
    print(f"Summary: {decision['summary']}")
    print(f"Next action: {decision['next_action']}")
    print(f"Audit JSON: {result['output_path']}")
    print("Checks:")
    for check in decision.get("checks", []):
        print(f"- {check['id']}: {check['status']} - {check['summary']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse an invoice PDF and run deterministic AP decisioning.")
    parser.add_argument("--pdf", type=Path, required=True, help="Invoice PDF to parse.")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Optional generated invoice manifest containing procurement context.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JSON audit output path. Defaults to <pdf>.decision.json.",
    )
    args = parser.parse_args()

    result = run_invoice_decision(
        pdf_path=args.pdf,
        manifest_path=args.manifest,
        output_path=args.output,
    )
    _print_summary(result)


if __name__ == "__main__":
    main()
