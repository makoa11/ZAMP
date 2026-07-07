from __future__ import annotations

import unittest
from datetime import date

from app.invoice_generator import generate_invoice_samples
from app.invoice_pdf import _PdfCanvas, _money, _text_width, render_invoice_pdf


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
