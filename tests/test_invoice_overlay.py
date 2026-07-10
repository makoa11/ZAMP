from __future__ import annotations

import io
import unittest

from pypdf import PdfReader

from app.invoice_overlay import (
    extract_pdf_word_bboxes,
    highlight_boxes_for_mode,
    iter_parse_bboxes,
    render_invoice_parse_overlay_pdf,
)
from app.invoice_parser import parse_invoice_pdf
from app.invoice_pdf import _PdfCanvas, _build_pdf
from tests.test_invoice_parser import _simple_invoice_pdf


def _page_content(pdf_content: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_content))
    content = reader.pages[0].get_contents()
    assert content is not None
    return content.get_data().decode("latin-1")


class InvoiceOverlayTests(unittest.TestCase):
    def test_overlay_pdf_adds_transparent_yellow_highlight_stream(self) -> None:
        original = _simple_invoice_pdf()
        parse_result = parse_invoice_pdf(original)

        overlay = render_invoice_parse_overlay_pdf(original, parse_result)

        reader = PdfReader(io.BytesIO(overlay))
        self.assertEqual(len(reader.pages), 1)
        self.assertEqual(overlay[:8], b"%PDF-1.4")
        page = reader.pages[0]
        resources = page["/Resources"]
        self.assertIn("/ZampHL", resources["/ExtGState"])
        stream_text = _page_content(overlay)
        self.assertIn("/ZampHL gs", stream_text)
        self.assertIn("1.0000 0.9200 0.0000 rg", stream_text)
        self.assertIn(" re f", stream_text)

    def test_word_mode_highlights_extracted_word_boxes_not_only_parser_evidence(self) -> None:
        original = _simple_invoice_pdf()
        parse_result = parse_invoice_pdf(original)

        parsed_boxes, _ = highlight_boxes_for_mode(original, parse_result, box_mode="parsed")
        word_boxes, _ = highlight_boxes_for_mode(original, parse_result, box_mode="words")
        overlay = render_invoice_parse_overlay_pdf(original, parse_result, box_mode="words", padding=0)

        self.assertGreater(len(word_boxes), len(parsed_boxes))
        self.assertEqual(len(word_boxes), len(extract_pdf_word_bboxes(original)[0]))
        self.assertGreaterEqual(_page_content(overlay).count(" re f"), len(word_boxes))

    def test_overlay_converts_parser_top_left_bbox_to_pdf_rect(self) -> None:
        canvas = _PdfCanvas(width_pt=200, height_pt=100, font_style="system")
        canvas.text(10, 10, "Invoice No. INV-1", size=6)
        original = _build_pdf([canvas])
        parse_result = {
            "fields": {
                "invoice_number": {
                    "page": 1,
                    "bbox": [10, 20, 30, 40],
                    "raw": "INV-1",
                    "value": "INV-1",
                },
                "line_items": [],
            },
            "pages": [{"page": 1, "width": 200, "height": 100, "text": ""}],
            "warnings": [],
        }

        overlay = render_invoice_parse_overlay_pdf(original, parse_result, padding=0)

        self.assertIn("10.000 60.000 20.000 20.000 re f", _page_content(overlay))

    def test_iter_parse_bboxes_collects_nested_field_evidence(self) -> None:
        parse_result = {
            "fields": {
                "invoice_number": {"page": 1, "bbox": [1, 2, 3, 4]},
                "line_items": [
                    {
                        "description": {"page": 1, "bbox": [5, 6, 7, 8]},
                        "amount": {"page": 1, "bbox": [9, 10, 11, 12]},
                    }
                ],
            }
        }

        boxes = iter_parse_bboxes(parse_result)

        self.assertEqual([box.source for box in boxes], [
            "invoice_number",
            "line_items.description",
            "line_items.amount",
        ])


if __name__ == "__main__":
    unittest.main()
