from __future__ import annotations

import unittest
from datetime import date

from app.invoice_generator import generate_invoice, generate_invoice_samples
from app.invoice_pdf import _PdfCanvas, _money, _render_table, _text_width, render_invoice_pdf


class InvoicePdfTests(unittest.TestCase):
    def test_render_invoice_pdf_returns_pdf_document(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4",
            count=2,
            seed=500,
            today=date(2026, 7, 7),
        )

        content = render_invoice_pdf(samples)

        self.assertTrue(content.startswith(b"%PDF-1.4"))
        self.assertIn(b"/Type /Catalog", content)
        self.assertIn(b"/Type /Page", content)
        self.assertIn(b"/Count 2", content)
        self.assertIn(b"/Encoding /WinAnsiEncoding", content)
        self.assertTrue(content.rstrip().endswith(b"%%EOF"))

    def test_pdf_contains_invoice_text_from_model(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4",
            count=1,
            seed=500,
            today=date(2026, 7, 7),
        )

        content = render_invoice_pdf(samples)

        self.assertIn(samples[0]["data"]["seller"]["name"].encode("latin-1"), content)
        self.assertIn(samples[0]["data"]["invoice_number"].encode("latin-1"), content)

    def test_pdf_renders_invoice_number_occlusion_stamp(self) -> None:
        sample = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            variation_index=17,
            today=date(2026, 7, 7),
        )

        content = render_invoice_pdf([sample])

        self.assertIn(b"APPROVED", content)
        self.assertEqual(
            sample["data"]["visual_artifacts"][-1]["scenario"],
            "invoice_number_seal_occlusion",
        )

    def test_pdf_terms_and_footer_render_distinct_copy(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4",
            count=1,
            seed=500,
            today=date(2026, 7, 7),
        )
        samples[0]["data"]["notes"] = "Terms copy only."
        samples[0]["data"]["footer_note"] = "Footer copy only."

        content = render_invoice_pdf(samples)

        self.assertIn(b"Terms copy only.", content)
        self.assertIn(b"Footer copy only.", content)
        self.assertIn(b"NOTICE", content)
        self.assertEqual(content.count(b"Terms copy only."), 1)

    def test_pdf_footer_without_footer_note_does_not_repeat_terms_copy(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4",
            count=1,
            seed=500,
            today=date(2026, 7, 7),
        )
        samples[0]["data"]["notes"] = "Terms copy should appear once."
        samples[0]["data"].pop("footer_note", None)

        content = render_invoice_pdf(samples)

        self.assertEqual(content.count(b"Terms copy should appear once."), 1)

    def test_render_invoice_pdf_supports_horizontal_split_pages(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4-third-horizontal",
            count=2,
            seed=500,
            today=date(2026, 7, 7),
        )

        content = render_invoice_pdf(samples)

        self.assertTrue(content.startswith(b"%PDF-1.4"))
        self.assertIn(b"/Count 2", content)
        self.assertIn(samples[0]["data"]["invoice_number"].encode("latin-1"), content)

    def test_pdf_table_headers_align_with_numeric_columns(self) -> None:
        class RecordingCanvas(_PdfCanvas):
            def __init__(self) -> None:
                super().__init__(width_pt=240, height_pt=180, font_style="system")
                self.text_calls: list[tuple[str, dict[str, object]]] = []

            def text(self, x_mm: float, y_mm: float, text: str, **kwargs: object) -> None:
                self.text_calls.append((text, kwargs))
                super().text(x_mm, y_mm, text, **kwargs)

        sample = {
            "template": {"accent": "#111827"},
            "data": {
                "currency": "USD",
                "formatting": {"money_style": "symbol-prefix-2dp", "decimals": 2},
                "items": [{"name": "Implementation", "quantity": 2, "unit_price": 100, "amount": 200}],
                "table": {
                    "columns": [
                        {"key": "item", "label": "Item"},
                        {"key": "quantity", "label": "Qty", "numeric": True},
                        {"key": "amount", "label": "Amount", "numeric": True},
                    ],
                    "total_in_table": False,
                },
            },
        }
        component = {"x_mm": 8, "y_mm": 8, "width_mm": 68, "height_mm": 24}
        canvas = RecordingCanvas()

        _render_table(canvas, component, sample)

        header_alignments = {
            text: kwargs.get("align")
            for text, kwargs in canvas.text_calls
            if text in {"Item", "Qty", "Amount"}
        }
        self.assertEqual(header_alignments["Item"], "left")
        self.assertEqual(header_alignments["Qty"], "right")
        self.assertEqual(header_alignments["Amount"], "right")

    def test_pdf_table_amount_boundary_collision_offsets_amount_values(self) -> None:
        class RecordingCanvas(_PdfCanvas):
            def __init__(self) -> None:
                super().__init__(width_pt=240, height_pt=180, font_style="system")
                self.text_calls: list[tuple[float, float, str, dict[str, object]]] = []

            def text(self, x_mm: float, y_mm: float, text: str, **kwargs: object) -> None:
                self.text_calls.append((x_mm, y_mm, text, kwargs))
                super().text(x_mm, y_mm, text, **kwargs)

        base_data = {
            "currency": "USD",
            "formatting": {"money_style": "symbol-prefix-2dp", "decimals": 2},
            "items": [{"name": "Implementation", "quantity": 2, "unit_price": 100, "amount": 200}],
            "table": {
                "columns": [
                    {"key": "item", "label": "Item"},
                    {"key": "quantity", "label": "Qty", "numeric": True},
                    {"key": "amount", "label": "Amount", "numeric": True},
                ],
                "total_in_table": False,
            },
        }
        normal = {"template": {"accent": "#111827"}, "data": base_data}
        collision_data = {
            **base_data,
            "table": {**base_data["table"], "visual_density": "amount_boundary_collision"},
        }
        collision = {"template": {"accent": "#111827"}, "data": collision_data}
        component = {"x_mm": 8, "y_mm": 8, "width_mm": 68, "height_mm": 24}
        normal_canvas = RecordingCanvas()
        collision_canvas = RecordingCanvas()

        _render_table(normal_canvas, component, normal)
        _render_table(collision_canvas, component, collision)

        normal_amount = next(call for call in normal_canvas.text_calls if call[2] == "$200.00")
        collision_amount = next(call for call in collision_canvas.text_calls if call[2] == "$200.00")
        self.assertGreater(collision_amount[0], normal_amount[0])
        self.assertGreater(collision_amount[1], normal_amount[1])

    def test_pdf_money_symbols_distinguish_symbol_from_code_styles(self) -> None:
        expected_symbols = {
            "EUR": "€",
            "GBP": "£",
            "CNY": "¥",
            "JPY": "¥",
        }
        for currency, symbol in expected_symbols.items():
            symbol_value = _money(
                1234.56,
                {
                    "currency": currency,
                    "formatting": {"money_style": "symbol-prefix-2dp", "decimals": 2},
                },
            )
            code_value = _money(
                1234.56,
                {
                    "currency": currency,
                    "formatting": {"money_style": "code-prefix-2dp", "decimals": 2},
                },
            )

            self.assertTrue(symbol_value.startswith(symbol))
            self.assertTrue(code_value.startswith(currency))
            self.assertNotEqual(symbol_value, code_value)

    def test_pdf_money_formats_inr_as_winansi_text(self) -> None:
        self.assertEqual(
            _money(
                1234.56,
                {
                    "currency": "INR",
                    "formatting": {"money_style": "symbol-prefix-2dp", "decimals": 2},
                },
            ),
            "Rs 1,234.56",
        )

    def test_pdf_stream_preserves_winansi_currency_symbols(self) -> None:
        canvas = _PdfCanvas(width_pt=120, height_pt=80, font_style="system")

        canvas.text(5, 5, "€ £ ¥", size=8)

        self.assertIn("€ £ ¥".encode("cp1252"), canvas.stream())

    def test_text_width_uses_font_metrics(self) -> None:
        self.assertGreater(
            _text_width("WW", 10, font="F1"),
            _text_width("ii", 10, font="F1"),
        )
        self.assertEqual(
            _text_width("WW", 10, font="F5"),
            _text_width("ii", 10, font="F5"),
        )


if __name__ == "__main__":
    unittest.main()
