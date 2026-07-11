from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.invoice_fixtures import write_invoice_manifest
from app.invoice_generator import generate_invoice
from app.invoice_pdf import render_invoice_pdf
from scripts.parse_test_pdfs import _pdf_paths
from scripts.run_test_invoice_pipeline import run_test_invoice_pipeline


class ParseTestPdfsScriptTests(unittest.TestCase):
    def test_pdf_paths_includes_any_pdf_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            expected = [
                root / "custom-client-drop.pdf",
                root / "INVOICE.PDF",
                root / "invoice-sample-0001-a4-ledger-clean.pdf",
            ]
            for path in expected:
                path.write_bytes(b"%PDF-1.4\n%%EOF\n")
            (root / "invoice-sample-0001-a4-ledger-clean.manifest.json").write_text("{}", encoding="utf-8")
            (root / "notes.txt").write_text("not a pdf", encoding="utf-8")

            self.assertEqual(_pdf_paths(root), sorted(expected))

    def test_full_synthetic_pipeline_writes_decision_audit_artifacts(self) -> None:
        sample = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4-half-horizontal",
            seed=123,
            variation_index=1,
            today=date(2026, 7, 7),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output_dir = root / "out"
            pdf_path = root / "partial.pdf"
            pdf_path.write_bytes(render_invoice_pdf([sample]))
            write_invoice_manifest(pdf_path, [sample])

            summary = run_test_invoice_pipeline(
                input_dir=root,
                output_dir=output_dir,
                limit=1,
            )

            file_summary = summary["files"][0]
            self.assertEqual(file_summary["expected_decision"], "approve_partial_consumption")
            self.assertEqual(file_summary["decision"], "approve_partial_consumption")
            for key in ("parsed_json", "overlay_pdf", "normalized_json", "decision_json", "audit_json"):
                self.assertTrue(Path(file_summary[key]).exists())

            decision_payload = json.loads(Path(file_summary["decision_json"]).read_text(encoding="utf-8"))
            self.assertTrue(decision_payload["matches_expected"])

    def test_pipeline_can_generate_then_parse_and_decide_in_one_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            input_dir = root / "generated"
            output_dir = root / "out"

            summary = run_test_invoice_pipeline(
                input_dir=input_dir,
                output_dir=output_dir,
                generate=True,
                pdf_count=3,
                seed=500,
                today=date(2026, 7, 7),
                box_mode="all",
            )

            self.assertEqual(summary["total"], 3)
            self.assertEqual(summary["generation"]["generated_pdf_count"], 3)
            self.assertEqual(summary["generation"]["seed"], 500)
            self.assertEqual(summary["generation"]["date"], "2026-07-07")
            self.assertEqual(summary["box_mode"], "all")
            self.assertEqual(len(list(input_dir.glob("*.pdf"))), 3)
            self.assertEqual(len(list(input_dir.glob("*.manifest.json"))), 3)
            self.assertTrue((output_dir / "summary.json").exists())
            for file_summary in summary["files"]:
                for key in ("parsed_json", "overlay_pdf", "normalized_json", "decision_json", "audit_json"):
                    self.assertTrue(Path(file_summary[key]).exists())


if __name__ == "__main__":
    unittest.main()
