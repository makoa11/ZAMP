from __future__ import annotations

import unittest

from app.invoice_ocr import (
    DocumentOcrPage,
    DocumentOcrResult,
    DocumentOcrWord,
    OcrRegionCandidate,
    RegionOcrText,
    RegionOcrUnavailable,
    apply_low_confidence_region_ocr,
    low_confidence_ocr_regions,
)
from app.invoice_parser import _replace_field_with_ocr_result, parse_invoice_pdf
from app.invoice_pdf import MM_TO_PT, _PdfCanvas, _build_pdf
from tests.test_invoice_parser import _simple_invoice_pdf


class FakeOcrEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[int, tuple[float, float, float, float]]] = []

    def ocr_region(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float],
    ) -> RegionOcrText:
        self.calls.append((page, bbox))
        return RegionOcrText(text=f"OCR page {page}", confidence=0.91, method="fake_region")


class PaymentAwareOcrEngine:
    def __init__(self) -> None:
        self.calls: list[tuple[int, tuple[float, float, float, float]]] = []

    def ocr_region(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float],
    ) -> RegionOcrText:
        self.calls.append((page, bbox))
        if bbox[1] > 500:
            return RegionOcrText(
                text="Payment Instructions ACH transfer **** 1234",
                confidence=0.95,
                method="fake_region",
            )
        if bbox[0] > 300:
            return RegionOcrText(text="USD 100.00", confidence=0.95, method="fake_region")
        return RegionOcrText(text="USD", confidence=0.95, method="fake_region")


class UnavailableOcrEngine:
    def ocr_region(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float],
    ) -> RegionOcrText:
        raise RegionOcrUnavailable("missing test OCR runtime")


class FakeFullDocumentOcrEngine:
    def __init__(self, *, include_due_date: bool = True) -> None:
        self.include_due_date = include_due_date
        self.calls = 0

    def ocr_document(self, content: bytes) -> DocumentOcrResult:
        self.calls += 1
        words = _full_invoice_ocr_words(include_due_date=self.include_due_date)
        return DocumentOcrResult(
            pages=[
                DocumentOcrPage(
                    page=1,
                    width=210 * MM_TO_PT,
                    height=297 * MM_TO_PT,
                    text=" ".join(word.text for word in words),
                    confidence=0.94,
                )
            ],
            words=words,
            confidence=0.94,
            method="fake_document",
        )


def _blank_pdf() -> bytes:
    canvas = _PdfCanvas(
        width_pt=210 * MM_TO_PT,
        height_pt=297 * MM_TO_PT,
        font_style="system",
    )
    canvas.rect(10, 10, 30, 20, fill="#ffffff", stroke="#111827")
    return _build_pdf([canvas])


def _invoice_without_due_date_pdf() -> bytes:
    canvas = _PdfCanvas(
        width_pt=210 * MM_TO_PT,
        height_pt=297 * MM_TO_PT,
        font_style="system",
    )
    canvas.text(15, 52, "From", size=6, bold=True)
    canvas.text(15, 57, "Acme Supplies LLC", size=7, bold=True)
    canvas.text(15, 72, "Bill To", size=6, bold=True)
    canvas.text(15, 77, "Beta Foods Inc", size=7, bold=True)
    canvas.text(118, 12, "Invoice No. INV-2026-0042", size=7, bold=True)
    canvas.text(118, 20, "Invoice Date 2026-06-10", size=7)
    canvas.text(15, 105, "Item", size=6, bold=True)
    canvas.text(92, 105, "Qty", size=6, bold=True)
    canvas.text(118, 105, "Rate", size=6, bold=True)
    canvas.text(158, 105, "Amount", size=6, bold=True)
    canvas.text(15, 115, "Platform license", size=6)
    canvas.text(96, 115, "2", size=6)
    canvas.text(118, 115, "USD 100.00", size=6)
    canvas.text(158, 115, "USD 200.00", size=6)
    canvas.text(120, 155, "Subtotal USD 200.00", size=7)
    canvas.text(120, 163, "Tax USD 16.00", size=7)
    canvas.text(120, 171, "Balance Due USD 216.00", size=7, bold=True)
    return _build_pdf([canvas])


def _full_invoice_ocr_words(*, include_due_date: bool = True) -> list[DocumentOcrWord]:
    lines: list[tuple[float, float, tuple[str, ...]]] = [
        (15, 52, ("From",)),
        (15, 57, ("Acme", "Supplies", "LLC")),
        (15, 72, ("Bill", "To")),
        (15, 77, ("Beta", "Foods", "Inc")),
        (335, 12, ("Invoice", "No.", "INV-2026-0042")),
        (335, 20, ("Invoice", "Date", "2026-06-10")),
        (15, 105, ("Item",)),
        (260, 105, ("Qty",)),
        (330, 105, ("Rate",)),
        (450, 105, ("Amount",)),
        (15, 115, ("Platform", "license")),
        (274, 115, ("2",)),
        (330, 115, ("USD", "100.00")),
        (450, 115, ("USD", "200.00")),
        (350, 155, ("Subtotal", "USD", "200.00")),
        (350, 163, ("Tax", "USD", "16.00")),
        (350, 171, ("Balance", "Due", "USD", "216.00")),
    ]
    if include_due_date:
        lines.insert(6, (335, 28, ("Due", "Date", "2026-07-10")))

    words: list[DocumentOcrWord] = []
    for x0, top, texts in lines:
        cursor = x0
        for text in texts:
            width = max(len(text) * 4.2, 4.2)
            words.append(
                DocumentOcrWord(
                    text=text,
                    page=1,
                    x0=round(cursor, 2),
                    top=top,
                    x1=round(cursor + width, 2),
                    bottom=top + 6,
                    confidence=0.94,
                )
            )
            cursor += width + 4
    return words


class InvoiceOcrTests(unittest.TestCase):
    def test_low_confidence_regions_are_padded_clamped_and_attached_to_fields(self) -> None:
        fields = {
            "invoice_number": {
                "raw": "old",
                "value": "old",
                "page": 1,
                "bbox": [1, 2, 10, 12],
                "confidence": 0.84,
                "method": "old_method",
            },
            "issue_date": {
                "page": 1,
                "bbox": [20, 20, 30, 30],
                "confidence": 0.85,
            },
            "seller": {
                "raw": "old seller",
                "value": "old seller",
                "page": 2,
                "bbox": [2, 2, 5, 5],
                "confidence": 82,
            },
            "line_items": [
                {
                    "quantity": {
                        "raw": "1",
                        "value": 1,
                        "page": 1,
                        "bbox": [95, 96, 99, 99],
                        "confidence": "0.82",
                    }
                }
            ],
        }
        pages = [
            {"page": 1, "width": 100, "height": 100},
            {"page": 2, "width": 50, "height": 50},
        ]
        engine = FakeOcrEngine()
        warnings: list[str] = []

        summary = apply_low_confidence_region_ocr(
            b"%PDF-1.4\nfixture",
            fields=fields,
            pages=pages,
            warnings=warnings,
            padding=5,
            engine=engine,
        )

        self.assertEqual(summary["status"], "completed")
        self.assertEqual(warnings, [])
        self.assertEqual(
            engine.calls,
            [
                (1, (0.0, 0.0, 15.0, 17.0)),
                (2, (0.0, 0.0, 10.0, 10.0)),
                (1, (90.0, 91.0, 100.0, 100.0)),
            ],
        )
        self.assertEqual([region["path"] for region in summary["regions"]], [
            "fields.invoice_number",
            "fields.seller",
            "fields.line_items[0].quantity",
        ])
        self.assertNotIn("ocr", fields["issue_date"])
        self.assertNotIn("ocr", fields["invoice_number"])
        self.assertEqual(fields["invoice_number"]["raw"], "OCR page 1")
        self.assertEqual(fields["invoice_number"]["value"], "OCR page 1")
        self.assertEqual(fields["invoice_number"]["confidence"], 0.91)
        self.assertEqual(fields["invoice_number"]["method"], "fake_region")
        self.assertEqual(fields["invoice_number"]["bbox"], [0.0, 0.0, 15.0, 17.0])
        self.assertEqual(fields["line_items"][0]["quantity"]["bbox"], [90.0, 91.0, 100, 100])

    def test_ocr_attempts_are_capped_after_priority_sorting(self) -> None:
        fields = {
            "line_items": [
                {
                    "unit_price": {
                        "raw": "USD 10.00",
                        "value": 10.0,
                        "page": 1,
                        "bbox": [70, 1, 90, 5],
                        "confidence": 0.2,
                    }
                }
            ],
            "invoice_number": {
                "raw": "old",
                "value": "old",
                "page": 1,
                "bbox": [1, 1, 10, 5],
                "confidence": 0.8,
            },
            "balance_due": {
                "raw": "USD 20.00",
                "value": 20.0,
                "page": 1,
                "bbox": [40, 1, 60, 5],
                "confidence": 0.4,
            },
        }
        engine = FakeOcrEngine()
        warnings: list[str] = []

        summary = apply_low_confidence_region_ocr(
            b"%PDF-1.4\nfixture",
            fields=fields,
            pages=[{"page": 1, "width": 100, "height": 100}],
            warnings=warnings,
            max_regions=2,
            engine=engine,
        )

        self.assertEqual(summary["candidate_count"], 3)
        self.assertEqual(summary["attempted_count"], 2)
        self.assertEqual(summary["capped_region_count"], 1)
        self.assertEqual(summary["skipped_count"], 1)
        self.assertEqual([region["path"] for region in summary["regions"]], [
            "fields.invoice_number",
            "fields.balance_due",
        ])
        self.assertEqual(len(engine.calls), 2)

    def test_low_confidence_region_collection_skips_invalid_or_high_confidence_fields(self) -> None:
        fields = {
            "high_confidence": {"page": 1, "bbox": [1, 1, 5, 5], "confidence": 0.9},
            "equal_threshold": {"page": 1, "bbox": [1, 1, 5, 5], "confidence": 0.85},
            "missing_bbox": {"page": 1, "confidence": 0.1},
            "invalid_bbox": {"page": 1, "bbox": [5, 5, 1, 1], "confidence": 0.1},
            "low_confidence": {"page": 1, "bbox": [1, 1, 5, 5], "confidence": 0.849},
        }

        candidates = low_confidence_ocr_regions(
            fields=fields,
            pages=[{"page": 1, "width": 20, "height": 20}],
            padding=2,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].path, ("low_confidence",))
        self.assertEqual(candidates[0].padded_bbox, (0.0, 0.0, 7.0, 7.0))

    def test_ocr_unavailable_returns_summary_and_warning_without_crashing_parse(self) -> None:
        fields = {
            "seller": {
                "page": 1,
                "bbox": [1, 1, 5, 5],
                "confidence": 0.4,
            }
        }
        warnings: list[str] = []

        summary = apply_low_confidence_region_ocr(
            b"%PDF-1.4\nfixture",
            fields=fields,
            pages=[{"page": 1, "width": 20, "height": 20}],
            warnings=warnings,
            engine=UnavailableOcrEngine(),
        )

        self.assertEqual(summary["status"], "unavailable")
        self.assertIn("missing test OCR runtime", summary["reason"])
        self.assertIn("Region OCR unavailable", warnings[0])
        self.assertEqual(summary["failed_count"], 1)
        self.assertEqual(summary["regions"][0]["reason"], "ocr_unavailable")
        self.assertNotIn("ocr", fields["seller"])

    def test_parser_ocr_replacement_normalizes_currency_in_place(self) -> None:
        fields = {
            "currency": {
                "raw": "old",
                "value": "USD",
                "page": 1,
                "bbox": [1, 1, 5, 5],
                "confidence": 0.6,
                "method": "currency_code_text",
            }
        }
        candidate = OcrRegionCandidate(
            path=("currency",),
            page=1,
            bbox=(1, 1, 5, 5),
            padded_bbox=(0, 0, 9, 9),
            confidence=0.6,
        )

        applied = _replace_field_with_ocr_result(
            fields,
            fields["currency"],
            candidate,
            RegionOcrText(text="EUR", confidence=0.93, method="fake_region"),
        )

        self.assertTrue(applied)
        self.assertEqual(fields["currency"]["raw"], "EUR")
        self.assertEqual(fields["currency"]["value"], "EUR")
        self.assertEqual(fields["currency"]["confidence"], 0.93)
        self.assertEqual(fields["currency"]["method"], "fake_region")
        self.assertEqual(fields["currency"]["bbox"], [0, 0, 9, 9])
        self.assertNotIn("ocr", fields["currency"])

    def test_parser_ocr_replacement_keeps_line_item_money_summary_in_sync(self) -> None:
        fields = {
            "currency": {"value": "USD"},
            "line_items": [
                {
                    "value": {
                        "description": "Service",
                        "quantity": 1,
                        "unit_price": 10.0,
                        "amount": 10.0,
                        "currency": "USD",
                    },
                    "amount": {
                        "raw": "USD 10.00",
                        "value": 10.0,
                        "amount": 10.0,
                        "currency": "USD",
                        "page": 1,
                        "bbox": [10, 10, 20, 20],
                        "confidence": 0.7,
                        "method": "word_table",
                    },
                }
            ],
        }
        candidate = OcrRegionCandidate(
            path=("line_items", 0, "amount"),
            page=1,
            bbox=(10, 10, 20, 20),
            padded_bbox=(8, 8, 22, 22),
            confidence=0.7,
        )

        applied = _replace_field_with_ocr_result(
            fields,
            fields["line_items"][0]["amount"],
            candidate,
            RegionOcrText(text="EUR 42.50", confidence=0.89, method="fake_region"),
        )

        self.assertTrue(applied)
        amount = fields["line_items"][0]["amount"]
        self.assertEqual(amount["raw"], "EUR 42.50")
        self.assertEqual(amount["amount"], 42.5)
        self.assertEqual(amount["value"], 42.5)
        self.assertEqual(amount["currency"], "EUR")
        self.assertEqual(fields["line_items"][0]["value"]["amount"], 42.5)
        self.assertEqual(fields["line_items"][0]["value"]["currency"], "EUR")

    def test_parser_ocr_replacement_rejects_low_confidence_text(self) -> None:
        fields = {
            "invoice_number": {
                "raw": "INV-OLD",
                "value": "INV-OLD",
                "page": 1,
                "bbox": [1, 1, 5, 5],
                "confidence": 0.82,
                "method": "static",
            }
        }
        candidate = OcrRegionCandidate(
            path=("invoice_number",),
            page=1,
            bbox=(1, 1, 5, 5),
            padded_bbox=(0, 0, 9, 9),
            confidence=0.82,
        )

        applied = _replace_field_with_ocr_result(
            fields,
            fields["invoice_number"],
            candidate,
            RegionOcrText(text="INV-NEW", confidence=0.83, method="fake_region"),
        )

        self.assertEqual(applied, "ocr_confidence_below_threshold")
        self.assertEqual(fields["invoice_number"]["value"], "INV-OLD")

    def test_parser_ocr_replacement_rejects_missing_confidence(self) -> None:
        fields = {
            "invoice_number": {
                "raw": "INV-OLD",
                "value": "INV-OLD",
                "page": 1,
                "bbox": [1, 1, 5, 5],
                "confidence": 0.82,
            }
        }
        candidate = OcrRegionCandidate(
            path=("invoice_number",),
            page=1,
            bbox=(1, 1, 5, 5),
            padded_bbox=(0, 0, 9, 9),
            confidence=0.82,
        )

        applied = _replace_field_with_ocr_result(
            fields,
            fields["invoice_number"],
            candidate,
            RegionOcrText(text="INV-NEW", confidence=None, method="fake_region"),
        )

        self.assertEqual(applied, "ocr_confidence_missing")
        self.assertEqual(fields["invoice_number"]["value"], "INV-OLD")

    def test_parse_invoice_pdf_can_disable_ocr_explicitly(self) -> None:
        engine = FakeOcrEngine()

        result = parse_invoice_pdf(
            _simple_invoice_pdf(),
            source_id="fixture:ocr-disabled",
            enable_ocr=False,
            ocr_engine=engine,
        )

        self.assertNotIn("ocr", result)
        self.assertEqual(engine.calls, [])
        self.assertEqual(result["fields"]["payment_instructions"]["value"], "ACH transfer **** 1234")

    def test_parse_invoice_pdf_runs_targeted_ocr_by_default_and_trims_payment_label(self) -> None:
        engine = PaymentAwareOcrEngine()

        result = parse_invoice_pdf(
            _simple_invoice_pdf(),
            source_id="fixture:ocr-default",
            ocr_engine=engine,
        )

        self.assertIn("ocr", result)
        self.assertTrue(result["ocr_used"])
        self.assertIn("payment_instructions", result["ocr_parts"])
        self.assertEqual(result["normal_model_failed_parts"], [])
        self.assertGreaterEqual(len(engine.calls), 1)
        self.assertGreaterEqual(result["ocr"]["attempted_count"], 1)
        self.assertGreaterEqual(result["ocr"]["applied_count"], 1)
        self.assertEqual(result["fields"]["payment_instructions"]["value"], "ACH transfer **** 1234")
        payment_regions = [
            region
            for region in result["ocr"]["regions"]
            if region["path"] == "fields.payment_instructions"
        ]
        self.assertEqual(payment_regions[0]["applied"], True)
        self.assertEqual(payment_regions[0]["original_value"], "ACH transfer **** 1234")

    def test_parse_invoice_pdf_uses_full_document_ocr_for_scanned_pdf(self) -> None:
        document_engine = FakeFullDocumentOcrEngine()

        result = parse_invoice_pdf(
            _blank_pdf(),
            source_id="fixture:scanned",
            document_ocr_engine=document_engine,
        )

        self.assertEqual(document_engine.calls, 1)
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["fields"]["invoice_number"]["value"], "INV-2026-0042")
        self.assertEqual(result["fields"]["due_date"]["value"], "2026-07-10")
        self.assertEqual(result["fields"]["balance_due"]["amount"], 216.0)
        self.assertTrue(result["ocr_used"])
        self.assertIn("invoice_number", result["ocr_parts"])
        self.assertIn("due_date", result["ocr_parts"])
        self.assertIn("invoice_number", result["normal_model_failed_parts"])
        self.assertEqual(result["ocr_failed_parts"], [])
        self.assertTrue(result["fields"]["seller"]["method"].startswith("full_document_ocr:"))
        self.assertEqual(result["pages"][0]["source"], "full_document_ocr")
        self.assertEqual(result["ocr"]["full_document"]["trigger"], "no_text_layer")

    def test_parse_invoice_pdf_uses_full_document_ocr_when_required_field_is_missing(self) -> None:
        document_engine = FakeFullDocumentOcrEngine()

        result = parse_invoice_pdf(
            _invoice_without_due_date_pdf(),
            source_id="fixture:missing-due-date",
            ocr_max_regions=0,
            document_ocr_engine=document_engine,
        )

        self.assertEqual(document_engine.calls, 1)
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["fields"]["due_date"]["value"], "2026-07-10")
        self.assertTrue(result["ocr_used"])
        self.assertIn("due_date", result["ocr_parts"])
        self.assertIn("due_date", result["normal_model_failed_parts"])
        self.assertEqual(result["ocr_failed_parts"], [])
        self.assertTrue(result["fields"]["due_date"]["method"].startswith("full_document_ocr:"))
        self.assertIn("due_date", result["ocr"]["full_document"]["missing_fields_before"])
        self.assertIn("due_date", result["ocr"]["full_document"]["applied_fields"])
        self.assertEqual(result["ocr"]["full_document"]["missing_fields_after"], [])

    def test_parse_invoice_pdf_routes_to_review_after_full_document_ocr_still_misses_required_data(self) -> None:
        document_engine = FakeFullDocumentOcrEngine(include_due_date=False)

        result = parse_invoice_pdf(
            _invoice_without_due_date_pdf(),
            source_id="fixture:review",
            ocr_max_regions=0,
            document_ocr_engine=document_engine,
        )

        self.assertEqual(document_engine.calls, 1)
        self.assertEqual(result["status"], "needs_review")
        self.assertTrue(result["ocr_used"])
        self.assertEqual(result["ocr_parts"], [])
        self.assertIn("due_date", result["normal_model_failed_parts"])
        self.assertIn("due_date", result["ocr_failed_parts"])
        self.assertEqual(result["review"]["reason"], "missing_required_normalized_data")
        self.assertIn("due_date", result["review"]["missing_fields"])
        self.assertIn("due_date", result["ocr"]["full_document"]["missing_fields_after"])


if __name__ == "__main__":
    unittest.main()
