from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.generate_test_pdfs import generate_test_pdfs


class GenerateTestPdfsTests(unittest.TestCase):
    def test_generate_test_pdfs_writes_all_paper_variations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                count=1,
                seed=500,
                today=date(2026, 7, 7),
            )

            self.assertEqual(
                [path.name for path in paths],
                [
                    "invoice-samples-a4.pdf",
                    "invoice-samples-a4-half-horizontal.pdf",
                    "invoice-samples-a4-third-horizontal.pdf",
                ],
            )
            for path in paths:
                content = path.read_bytes()
                self.assertTrue(content.startswith(b"%PDF-1.4"))
                self.assertTrue(content.rstrip().endswith(b"%%EOF"))

    def test_generate_test_pdfs_can_write_requested_number_of_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                pdf_count=5,
                seed=500,
                today=date(2026, 7, 7),
            )

            self.assertEqual(
                [path.name for path in paths],
                [
                    "invoice-sample-0001-a4.pdf",
                    "invoice-sample-0002-a4-half-horizontal.pdf",
                    "invoice-sample-0003-a4-third-horizontal.pdf",
                    "invoice-sample-0004-a4.pdf",
                    "invoice-sample-0005-a4-half-horizontal.pdf",
                ],
            )
            self.assertEqual(len(list(Path(temp_dir).glob("*.pdf"))), 5)
            for path in paths:
                content = path.read_bytes()
                self.assertTrue(content.startswith(b"%PDF-1.4"))
                self.assertIn(b"/Count 1", content)
                self.assertTrue(content.rstrip().endswith(b"%%EOF"))

    def test_stress_cases_and_pdf_count_are_mutually_exclusive(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "--stress-cases cannot be combined with --pdf-count"):
                generate_test_pdfs(
                    output_dir=Path(temp_dir),
                    pdf_count=5,
                    stress_cases=True,
                )

    def test_generate_test_pdfs_can_write_expected_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                pdf_count=2,
                seed=500,
                today=date(2026, 7, 7),
                write_manifests=True,
            )

            for path in paths:
                manifest_path = path.with_suffix(".manifest.json")
                self.assertTrue(manifest_path.exists())
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["schema_version"], 1)
                self.assertEqual(manifest["pdf"]["filename"], path.name)
                self.assertEqual(manifest["pdf"]["page_count"], 1)
                self.assertEqual(len(manifest["documents"]), 1)
                document = manifest["documents"][0]
                self.assertTrue(document["invoice_number"])
                self.assertGreaterEqual(len(document["line_items"]), 2)
                self.assertIn("balance_due", document["amounts"])
                self.assertIn("visible_value", document["amounts"]["balance_due"])

    def test_generate_test_pdfs_can_write_stress_cases_with_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                seed=500,
                today=date(2026, 7, 7),
                stress_cases=True,
                write_manifests=True,
            )

            self.assertEqual(
                [path.name for path in paths],
                [
                    "invoice-stress-multipage-continuation.pdf",
                    "invoice-stress-ambiguous-labels-mixed-totals.pdf",
                    "invoice-stress-currency-glyphs.pdf",
                ],
            )

            multipage = json.loads(paths[0].with_suffix(".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(multipage["pdf"]["page_count"], 3)
            self.assertIn("multi_page_invoice", multipage["challenge_tags"])
            self.assertIn("table_continuation_across_pages", multipage["challenge_tags"])
            self.assertIn("notes_near_table_bounds", multipage["challenge_tags"])
            self.assertEqual(len(multipage["documents"]), 1)
            self.assertEqual(len(multipage["documents"][0]["line_items"]), 24)
            self.assertTrue(multipage["documents"][0]["pages"][0]["table_continues_after_page"])
            self.assertTrue(multipage["documents"][0]["pages"][1]["table_continues_from_previous_page"])

            ambiguous = json.loads(paths[1].with_suffix(".manifest.json").read_text(encoding="utf-8"))
            labels = [
                document["labels"].get("seller", "")
                for document in ambiguous["documents"]
            ] + [
                document["labels"].get("buyer", "")
                for document in ambiguous["documents"]
            ]
            placements = {document["table"]["total_placement"] for document in ambiguous["documents"]}
            self.assertIn("ambiguous_entity_labels", ambiguous["challenge_tags"])
            self.assertIn("mixed_total_positions", ambiguous["challenge_tags"])
            self.assertTrue({"Account", "To", "Source", "Entity"}.issubset(set(labels)))
            self.assertIn("side_panel", placements)
            self.assertIn("table_row", placements)

            currency = json.loads(paths[2].with_suffix(".manifest.json").read_text(encoding="utf-8"))
            currencies = {document["currency"] for document in currency["documents"]}
            self.assertIn("messy_currency_glyphs", currency["challenge_tags"])
            self.assertIn("localized_decimal_separator", currency["challenge_tags"])
            self.assertTrue({"INR", "EUR", "CNY", "JPY", "MXN"}.issubset(currencies))


if __name__ == "__main__":
    unittest.main()
