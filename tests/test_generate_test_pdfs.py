from __future__ import annotations

import json
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path

from app.generate_test_pdfs import (
    DEFAULT_PDF_COUNT,
    DEFAULT_STANDARD_PDF_COUNT,
    DEFAULT_STRESS_PDF_COUNT,
    PAPER_TEMPLATE_COMBINATION_COUNT,
    generate_test_pdfs,
    iter_invoice_corpus,
)
from app.invoice_fixtures import build_invoice_manifest
from app.invoice_generator import BASE_TEMPLATES, PAPER_FORMATS


PARTIAL_PO_TAGS = {"partial_po_consumption", "split_po_billing"}


def _manifest(path: Path) -> dict[str, object]:
    return json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))


def _paper_template_pair(path: Path) -> tuple[str, str]:
    tail = path.stem.removeprefix("invoice-sample-")
    _, _, descriptor = tail.partition("-")
    for paper in sorted(PAPER_FORMATS, key=lambda item: len(item.slug), reverse=True):
        prefix = f"{paper.slug}-"
        if descriptor.startswith(prefix):
            return paper.slug, descriptor[len(prefix) :]
    raise AssertionError(f"Could not parse paper/template pair from {path.name}")


def _standard_paths(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.name.startswith("invoice-sample-")]


def _stress_paths(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.name.startswith("invoice-stress-")]


class GenerateTestPdfsTests(unittest.TestCase):
    def test_default_generation_writes_150_standard_and_100_stress_pdfs(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                seed=500,
                today=date(2026, 7, 7),
            )

            self.assertEqual(len(paths), DEFAULT_PDF_COUNT)
            self.assertEqual(len(list(Path(temp_dir).glob("*.pdf"))), DEFAULT_PDF_COUNT)
            self.assertEqual(
                len(list(Path(temp_dir).glob("*.manifest.json"))),
                DEFAULT_PDF_COUNT,
            )
            standard_paths = _standard_paths(paths)
            stress_paths = _stress_paths(paths)
            self.assertEqual(len(standard_paths), DEFAULT_STANDARD_PDF_COUNT)
            self.assertEqual(len(stress_paths), DEFAULT_STRESS_PDF_COUNT)
            self.assertEqual(
                [path.name for path in stress_paths[:3]],
                [
                    "invoice-stress-0001-multipage-continuation.pdf",
                    "invoice-stress-0002-ambiguous-labels-mixed-totals.pdf",
                    "invoice-stress-0003-currency-glyphs.pdf",
                ],
            )
            self.assertEqual(
                stress_paths[-1].name,
                "invoice-stress-0100-multipage-continuation.pdf",
            )
            for path in [paths[0], paths[-1]]:
                content = path.read_bytes()
                self.assertTrue(content.startswith(b"%PDF-1.4"))
                self.assertTrue(content.rstrip().endswith(b"%%EOF"))

    def test_standard_corpus_covers_every_paper_template_pair(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                seed=500,
                today=date(2026, 7, 7),
            )
            standard_paths = _standard_paths(paths)
            expected_pairs = {
                (paper.slug, template.slug)
                for paper in PAPER_FORMATS
                for template in BASE_TEMPLATES
            }
            pair_counts = Counter(_paper_template_pair(path) for path in standard_paths)

            self.assertEqual(len(standard_paths), DEFAULT_STANDARD_PDF_COUNT)
            self.assertEqual(set(pair_counts), expected_pairs)
            self.assertGreaterEqual(min(pair_counts.values()), 3)
            self.assertEqual(
                [path.name for path in standard_paths[:6]],
                [
                    "invoice-sample-0001-a4-ledger-clean.pdf",
                    "invoice-sample-0002-a4-half-horizontal-ledger-clean.pdf",
                    "invoice-sample-0003-a4-third-horizontal-ledger-clean.pdf",
                    "invoice-sample-0004-a4-north-star.pdf",
                    "invoice-sample-0005-a4-half-horizontal-north-star.pdf",
                    "invoice-sample-0006-a4-third-horizontal-north-star.pdf",
                ],
            )

    def test_repeat_rounds_shift_capture_profile_and_invoice_dates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                seed=500,
                today=date(2026, 7, 7),
            )
            standard_paths = _standard_paths(paths)
            first = _manifest(standard_paths[0])["documents"][0]
            repeat = _manifest(standard_paths[PAPER_TEMPLATE_COMBINATION_COUNT])["documents"][0]

            self.assertEqual(
                _paper_template_pair(standard_paths[0]),
                _paper_template_pair(standard_paths[PAPER_TEMPLATE_COMBINATION_COUNT]),
            )
            self.assertNotEqual(first["table"]["variant"], repeat["table"]["variant"])
            self.assertNotEqual(first["issue_date"]["value"], repeat["issue_date"]["value"])

    def test_generate_test_pdfs_can_write_small_requested_total_with_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                pdf_count=5,
                seed=500,
                today=date(2026, 7, 7),
            )

            self.assertEqual(len(paths), 5)
            self.assertEqual(len(list(Path(temp_dir).glob("*.pdf"))), 5)
            self.assertEqual(len(list(Path(temp_dir).glob("*.manifest.json"))), 5)
            self.assertEqual(len(_standard_paths(paths)), 3)
            self.assertEqual(len(_stress_paths(paths)), 2)
            for path in paths:
                manifest = _manifest(path)
                self.assertEqual(manifest["schema_version"], 1)
                self.assertEqual(manifest["pdf"]["filename"], path.name)
                self.assertTrue(path.read_bytes().startswith(b"%PDF-1.4"))

    def test_standard_manifest_preserves_partial_po_ap_edge_case_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                pdf_count=5,
                seed=500,
                today=date(2026, 7, 7),
            )
            partial_path = next(
                path
                for path in paths
                if path.name == "invoice-sample-0002-a4-half-horizontal-ledger-clean.pdf"
            )
            manifest = _manifest(partial_path)
            document = manifest["documents"][0]
            ap_context = document["ap_context"]
            edge_case = document["edge_cases"][0]

            self.assertTrue(PARTIAL_PO_TAGS.issubset(set(manifest["challenge_tags"])))
            self.assertTrue(PARTIAL_PO_TAGS.issubset(set(document["challenge_tags"])))
            self.assertEqual(document["purchase_order"], "PO-10000-PART")
            self.assertEqual(ap_context["scenario"], "split_po_partial_billing")
            self.assertEqual(ap_context["context"]["po_authorized_total"], "10000.00")
            self.assertEqual(ap_context["context"]["po_previously_consumed"], "3000.00")
            self.assertEqual(ap_context["context"]["po_remaining_before_invoice"], "7000.00")
            self.assertEqual(
                ap_context["context"]["previous_related_document"]["invoice_number"],
                "INV-PO10000-0001",
            )
            self.assertEqual(
                ap_context["context"]["client_database"]["previous_related_documents"][0]["applied_to_po"],
                "3000.00",
            )
            self.assertEqual(
                ap_context["context"]["vendor_database"]["previous_related_documents"][0]["applied_to_po"],
                "3000.00",
            )
            self.assertEqual(ap_context["expected"]["decision"], "approve_partial_consumption")
            self.assertEqual(ap_context["expected"]["remaining_after_invoice"], "3000.00")
            self.assertTrue(ap_context["expected"]["requires_client_vendor_match"])
            self.assertEqual(edge_case, ap_context)

    def test_corpus_iterator_matches_generated_pdf_names_and_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                pdf_count=5,
                seed=500,
                today=date(2026, 7, 7),
            )
            entries = list(
                iter_invoice_corpus(
                    pdf_count=5,
                    seed=500,
                    today=date(2026, 7, 7),
                )
            )

            self.assertEqual([path.name for path in paths], [entry.pdf_filename for entry in entries])
            for path, entry in zip(paths, entries):
                expected_manifest = build_invoice_manifest(
                    entry.samples,
                    pdf_filename=entry.pdf_filename,
                    suite=entry.suite,
                    fixture_slug=entry.fixture_slug,
                )
                self.assertEqual(_manifest(path), expected_manifest)

    def test_pdf_count_must_leave_room_for_stress_cases(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "pdf-count must be at least 2"):
                generate_test_pdfs(
                    output_dir=Path(temp_dir),
                    pdf_count=1,
                )

    def test_stress_fixture_families_are_written_with_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_test_pdfs(
                output_dir=Path(temp_dir),
                pdf_count=8,
                seed=500,
                today=date(2026, 7, 7),
            )
            stress_paths = _stress_paths(paths)

            self.assertEqual(
                [path.name for path in stress_paths],
                [
                    "invoice-stress-0001-multipage-continuation.pdf",
                    "invoice-stress-0002-ambiguous-labels-mixed-totals.pdf",
                    "invoice-stress-0003-currency-glyphs.pdf",
                ],
            )

            multipage = _manifest(stress_paths[0])
            self.assertEqual(multipage["pdf"]["page_count"], 3)
            self.assertIn("multi_page_invoice", multipage["challenge_tags"])
            self.assertIn("table_continuation_across_pages", multipage["challenge_tags"])
            self.assertIn("notes_near_table_bounds", multipage["challenge_tags"])
            self.assertEqual(len(multipage["documents"]), 1)
            self.assertEqual(len(multipage["documents"][0]["line_items"]), 24)
            self.assertTrue(multipage["documents"][0]["pages"][0]["table_continues_after_page"])
            self.assertTrue(multipage["documents"][0]["pages"][1]["table_continues_from_previous_page"])

            ambiguous = _manifest(stress_paths[1])
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

            currency = _manifest(stress_paths[2])
            currencies = {document["currency"] for document in currency["documents"]}
            self.assertIn("messy_currency_glyphs", currency["challenge_tags"])
            self.assertIn("localized_decimal_separator", currency["challenge_tags"])
            self.assertTrue({"INR", "EUR", "CNY", "JPY", "MXN"}.issubset(currencies))


if __name__ == "__main__":
    unittest.main()
