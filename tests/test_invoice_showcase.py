from __future__ import annotations

import unittest
from collections import Counter
from datetime import date

from app.invoice_degradation import DEFAULT_DEGRADATION_PROFILES
from app.invoice_generator import (
    AP_EDGE_CASE_SCENARIOS,
    BASE_TEMPLATES,
    CAPTURE_PROFILES,
    PAPER_FORMATS,
)
from app.invoice_showcase import (
    FIXTURE_PAGE_COUNTS,
    SHOWCASE_DOCUMENTS,
    showcase_samples,
)
from app.templates import invoice_showcase_page


class InvoiceShowcaseTests(unittest.TestCase):
    def test_catalog_has_one_pdf_per_variance_type(self) -> None:
        group_counts = Counter(document.group for document in SHOWCASE_DOCUMENTS)

        self.assertEqual(
            group_counts,
            {
                "Clean invoice types": len(BASE_TEMPLATES),
                "Accounts payable scenarios": len(AP_EDGE_CASE_SCENARIOS) - 1,
                "Rendering stress scenarios": 2 + len(FIXTURE_PAGE_COUNTS),
                "Page size variants": len(PAPER_FORMATS),
                "Scan and OCR stress": len(DEFAULT_DEGRADATION_PROFILES),
            },
        )
        self.assertEqual(
            len({document.slug for document in SHOWCASE_DOCUMENTS}),
            len(SHOWCASE_DOCUMENTS),
        )

    def test_clean_types_cover_every_template_and_capture_profile_once(self) -> None:
        clean_documents = [
            document for document in SHOWCASE_DOCUMENTS if document.group == "Clean invoice types"
        ]
        samples = [
            showcase_samples(document.slug, today=date(2026, 7, 13))[0]
            for document in clean_documents
        ]

        self.assertTrue(all(document.page_count == 1 for document in clean_documents))
        self.assertEqual(
            {sample["template"]["slug"] for sample in samples},
            {template.slug for template in BASE_TEMPLATES},
        )
        self.assertEqual(
            {sample["data"]["capture_profile"] for sample in samples},
            {str(profile["name"]) for profile in CAPTURE_PROFILES},
        )
        self.assertTrue(all("edge_cases" not in sample["data"] for sample in samples))

    def test_each_ap_document_contains_exactly_its_named_scenario(self) -> None:
        ap_documents = [
            document
            for document in SHOWCASE_DOCUMENTS
            if document.group == "Accounts payable scenarios"
        ]
        scenarios = set()
        for document in ap_documents:
            samples = showcase_samples(document.slug, today=date(2026, 7, 13))
            self.assertEqual(len(samples), 1)
            scenario = samples[0]["data"]["ap_context"]["scenario"]
            self.assertEqual(document.slug, f"ap-{scenario}")
            self.assertNotIn("visual_artifacts", samples[0]["data"])
            scenarios.add(scenario)

        self.assertEqual(scenarios, set(AP_EDGE_CASE_SCENARIOS) - {"none"})

    def test_visual_stress_documents_each_contain_one_named_artifact(self) -> None:
        expected = {
            "stress-table-amount-boundary-collision": "table_amount_boundary_collision",
            "stress-invoice-number-seal-occlusion": "invoice_number_seal_occlusion",
        }
        for slug, scenario in expected.items():
            samples = showcase_samples(slug, today=date(2026, 7, 13))
            artifacts = samples[0]["data"].get("visual_artifacts", [])
            self.assertEqual([artifact["scenario"] for artifact in artifacts], [scenario])
            self.assertNotIn("ap_context", samples[0]["data"])

    def test_fixture_stress_documents_preserve_scenario_page_counts(self) -> None:
        for fixture_slug, page_count in FIXTURE_PAGE_COUNTS.items():
            samples = showcase_samples(f"stress-{fixture_slug}", today=date(2026, 7, 13))
            self.assertEqual(len(samples), page_count)

    def test_page_size_documents_each_use_their_named_paper(self) -> None:
        for paper in PAPER_FORMATS:
            samples = showcase_samples(f"paper-{paper.slug}", today=date(2026, 7, 13))
            self.assertEqual(len(samples), 1)
            self.assertEqual(samples[0]["paper"]["slug"], paper.slug)

    def test_scan_documents_cover_every_degradation_profile(self) -> None:
        scan_slugs = {
            document.slug
            for document in SHOWCASE_DOCUMENTS
            if document.group == "Scan and OCR stress"
        }

        self.assertEqual(
            scan_slugs,
            {f"degraded-{profile.name}" for profile in DEFAULT_DEGRADATION_PROFILES},
        )

    def test_showcase_page_exposes_viewer_free_page_images(self) -> None:
        documents = [
            {
                "slug": document.slug,
                "title": document.title,
                "group": document.group,
                "description": document.description,
                "page_count": document.page_count,
                "tags": document.tags,
            }
            for document in SHOWCASE_DOCUMENTS
        ]

        content = invoice_showcase_page(documents=documents)

        expected_page_count = sum(document.page_count for document in SHOWCASE_DOCUMENTS)
        self.assertEqual(content.count('class="showcase-page"'), expected_page_count)
        self.assertIn('/showcase/clean-ledger-clean/pages/1.png', content)
        self.assertIn('/showcase/ap-credit_memo_negative_balance/pages/1.png', content)
        self.assertIn('/showcase/stress-currency-glyphs/pages/5.png', content)
        self.assertIn('/showcase/paper-a4-third-horizontal/pages/1.png', content)
        self.assertIn('/showcase/degraded-low-contrast-skew/pages/1.png', content)
        self.assertNotIn("<iframe", content)
        self.assertNotIn(".pdf#", content)
        self.assertNotIn("<button", content)
        self.assertNotIn('href="/showcase', content)
        self.assertNotIn('href="/dashboard', content)


if __name__ == "__main__":
    unittest.main()
