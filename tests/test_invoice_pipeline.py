from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.invoice_pipeline import (
    blocking_validation_failures,
    profile_document_pages,
    validate_invoice_fields,
)
from app.invoice_degradation import DegradationProfile, degrade_pdf_to_image_pdf
from tests.test_invoice_parser import _simple_invoice_pdf


def _word(text: str, *, page: int = 1, top: float = 10.0) -> SimpleNamespace:
    return SimpleNamespace(text=text, page=page, x0=10.0, top=top, x1=30.0, bottom=top + 8.0)


def _field(value: object, **extra: object) -> dict[str, object]:
    return {"value": value, **extra}


class InvoicePipelineTests(unittest.TestCase):
    def test_profiles_native_scanned_and_hybrid_pages(self) -> None:
        native_words = [_word(f"word-{index}") for index in range(25)]
        profiles = profile_document_pages(
            [
                {"page": 1, "text": "native"},
                {"page": 2, "text": ""},
                {"page": 3, "text": "sparse"},
            ],
            [*native_words, _word("one", page=3), _word("two", page=3), _word("three", page=3)],
            page_char_counts={1: 100, 2: 0, 3: 20},
        )

        self.assertEqual([profile.route for profile in profiles], ["native_text", "local_ocr", "hybrid"])
        self.assertIn("no_usable_native_text", profiles[1].reasons)
        self.assertIn("sparse_native_text", profiles[2].reasons)

    def test_validation_blocks_inconsistent_dates_and_amounts(self) -> None:
        fields = {
            "issue_date": _field("2026-07-12"),
            "due_date": _field("2026-07-01"),
            "currency": _field("USD"),
            "subtotal": _field(100.0, amount=100.0, currency="USD"),
            "tax": _field(10.0, amount=10.0, currency="USD"),
            "balance_due": _field(90.0, amount=90.0, currency="EUR"),
            "line_items": [
                {"amount": _field(40.0, amount=40.0, currency="USD")},
                {"amount": _field(50.0, amount=50.0, currency="USD")},
            ],
        }

        checks = validate_invoice_fields(fields)

        self.assertEqual(
            set(blocking_validation_failures(checks)),
            {"date_order", "amount_composition", "line_item_sum", "currency_consistency"},
        )

    def test_validation_accepts_reconciled_invoice(self) -> None:
        fields = {
            "issue_date": _field("2026-07-01"),
            "due_date": _field("2026-07-31"),
            "currency": _field("USD"),
            "subtotal": _field(100.0, amount=100.0, currency="USD"),
            "discount": _field(5.0, amount=5.0, currency="USD"),
            "tax": _field(9.5, amount=9.5, currency="USD"),
            "shipping": _field(2.0, amount=2.0, currency="USD"),
            "paid": _field(6.5, amount=6.5, currency="USD"),
            "balance_due": _field(100.0, amount=100.0, currency="USD"),
            "line_items": [{"amount": _field(100.0, amount=100.0, currency="USD")}],
        }

        self.assertEqual(blocking_validation_failures(validate_invoice_fields(fields)), [])

    def test_degradation_produces_image_only_pdf(self) -> None:
        import io

        import pdfplumber

        degraded = degrade_pdf_to_image_pdf(
            _simple_invoice_pdf(),
            profile=DegradationProfile(name="unit-scan", dpi=120, jpeg_quality=70),
            seed=7,
        )

        with pdfplumber.open(io.BytesIO(degraded)) as document:
            self.assertEqual(len(document.pages), 1)
            self.assertEqual(document.pages[0].chars, [])
            self.assertEqual(document.pages[0].extract_text() or "", "")


if __name__ == "__main__":
    unittest.main()
