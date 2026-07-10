from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest
from datetime import date

from app.generate_test_pdfs import iter_invoice_corpus
from app.invoice_generator import generate_invoice
from app.invoice_overlay import highlight_boxes_for_mode
from app.invoice_parser import (
    Word,
    _best_money_field,
    _build_lines,
    _group_words_by_y,
    _infer_currency,
    _infer_table_header,
    _line_items_from_word_tables,
    _parse_amount,
    _parse_date,
    _party_field,
    parse_invoice_pdf,
)
from app.invoice_pdf import MM_TO_PT, _PdfCanvas, _build_pdf, render_invoice_pdf


HAS_PDFPLUMBER = importlib.util.find_spec("pdfplumber") is not None
SAMPLE_0001_A4 = Path("storage/test_pdfs/invoice-sample-0001-a4-ledger-clean.pdf")
SAMPLE_0002_HALF = Path("storage/test_pdfs/invoice-sample-0002-a4-half-horizontal-ledger-clean.pdf")
SAMPLE_0005_HALF = Path("storage/test_pdfs/invoice-sample-0005-a4-half-horizontal-north-star.pdf")
SAMPLE_0006_THIRD = Path("storage/test_pdfs/invoice-sample-0006-a4-third-horizontal-north-star.pdf")


def _word(text: str, x0: float, top: float, x1: float | None = None, *, page: int = 1) -> Word:
    return Word(
        text=text,
        page=page,
        x0=x0,
        top=top,
        x1=x1 if x1 is not None else x0 + max(len(text) * 4.0, 4.0),
        bottom=top + 6,
    )


def _simple_invoice_pdf() -> bytes:
    canvas = _PdfCanvas(
        width_pt=210 * MM_TO_PT,
        height_pt=297 * MM_TO_PT,
        font_style="system",
    )
    canvas.text(15, 12, "Acme Supplies LLC", size=10, bold=True)
    canvas.text(15, 52, "From", size=6, bold=True)
    canvas.text(15, 57, "Acme Supplies LLC", size=7, bold=True)
    canvas.text(15, 62, "billing@acme.example", size=6)
    canvas.text(15, 72, "Bill To", size=6, bold=True)
    canvas.text(15, 77, "Beta Foods Inc", size=7, bold=True)
    canvas.text(15, 82, "ap@beta.example", size=6)

    canvas.text(118, 12, "Invoice No. INV-2026-0042", size=7, bold=True)
    canvas.text(118, 20, "Invoice Date 2026-06-10", size=7)
    canvas.text(118, 28, "Due Date 2026-07-10", size=7)
    canvas.text(118, 36, "PO PO-7788", size=7)
    canvas.text(118, 44, "Terms Net 30", size=7)

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
    canvas.text(15, 190, "Payment Instructions", size=6, bold=True)
    canvas.text(15, 196, "ACH transfer **** 1234", size=6)
    return _build_pdf([canvas])


def _consignor_consignee_invoice_pdf() -> bytes:
    canvas = _PdfCanvas(
        width_pt=210 * MM_TO_PT,
        height_pt=120 * MM_TO_PT,
        font_style="system",
    )
    canvas.text(15, 12, "Consignor", size=6, bold=True)
    canvas.text(15, 20, "Alpha Logistics LLC", size=7, bold=True)
    canvas.text(15, 28, "10 Port Road", size=6)
    canvas.text(15, 36, "Seattle, WA 98121", size=6)
    canvas.text(85, 12, "Consignee", size=6, bold=True)
    canvas.text(85, 20, "Beta Foods Inc", size=7, bold=True)
    canvas.text(85, 28, "88 Market Lane", size=6)
    canvas.text(85, 36, "Portland, OR 97201", size=6)
    canvas.text(150, 12, "Invoice No. CCI-1", size=6)
    canvas.text(150, 20, "Invoice Date 2026-07-01", size=6)

    canvas.text(15, 58, "Item", size=6, bold=True)
    canvas.text(92, 58, "Qty", size=6, bold=True)
    canvas.text(118, 58, "Rate", size=6, bold=True)
    canvas.text(158, 58, "Amount", size=6, bold=True)
    canvas.text(15, 68, "Freight service", size=6)
    canvas.text(96, 68, "1", size=6)
    canvas.text(118, 68, "USD 250.00", size=6)
    canvas.text(158, 68, "USD 250.00", size=6)
    canvas.text(120, 92, "Balance Due USD 250.00", size=7, bold=True)
    return _build_pdf([canvas])


def _previous_related_document(database: dict[str, object], po_number: str) -> dict[str, object]:
    documents = database.get("previous_related_documents")
    if not isinstance(documents, list):
        raise AssertionError("AP database context is missing previous_related_documents.")
    matches = [
        document
        for document in documents
        if isinstance(document, dict) and document.get("purchase_order") == po_number
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"Expected one previous related document for {po_number}, found {len(matches)}."
        )
    return matches[0]


def _manifest_document(pdf_path: Path) -> dict[str, object]:
    manifest_path = pdf_path.with_suffix(".manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents or not isinstance(documents[0], dict):
        raise AssertionError(f"Manifest {manifest_path} is missing documents[0].")
    return documents[0]


def _forbidden_visible_ap_fragments(ap_context: dict[str, object]) -> list[str]:
    context = ap_context["context"]
    expected = ap_context["expected"]
    if not isinstance(context, dict) or not isinstance(expected, dict):
        raise AssertionError("AP context is missing context or expected metadata.")
    previous_document = context["previous_related_document"]
    if not isinstance(previous_document, dict):
        raise AssertionError("AP context is missing previous_related_document metadata.")

    return [
        "AP prev",
        str(previous_document["invoice_number"]),
        str(expected["decision"]),
        "client/vendor match",
        "remaining PO balance",
        "partially consume",
    ]


def _assert_manifest_parties(test_case: unittest.TestCase, pdf_path: Path, fields: dict[str, object]) -> None:
    document = _manifest_document(pdf_path)
    seller = document.get("seller")
    buyer = document.get("buyer")
    if not isinstance(seller, dict) or not isinstance(buyer, dict):
        raise AssertionError(f"Manifest {pdf_path.with_suffix('.manifest.json')} is missing parties.")

    for party_key, expected_party, opposite_party in (
        ("seller", seller, buyer),
        ("buyer", buyer, seller),
    ):
        parsed_party = fields.get(party_key)
        test_case.assertIsInstance(parsed_party, dict)
        raw = str(parsed_party.get("raw") or "")
        test_case.assertTrue(raw)
        for expected_key in ("name", "line1"):
            test_case.assertIn(str(expected_party[expected_key]), raw)
            test_case.assertNotIn(str(opposite_party[expected_key]), raw)


class ParserPrimitiveTests(unittest.TestCase):
    def test_money_parser_handles_symbol_code_and_comma_decimal(self) -> None:
        self.assertEqual(_parse_amount("1,234.56"), 1234.56)
        self.assertEqual(_parse_amount("1.234,56"), 1234.56)
        self.assertEqual(_parse_amount("(48.00)"), -48.0)

    def test_date_parser_normalizes_common_invoice_dates(self) -> None:
        self.assertEqual(_parse_date("Invoice Date 2026-06-10"), ("2026-06-10", "2026-06-10"))
        self.assertEqual(_parse_date("Due 10 Jul 2026"), ("10 Jul 2026", "2026-07-10"))
        self.assertEqual(_parse_date("Doc Date 20260710"), ("20260710", "2026-07-10"))

    def test_ambiguous_party_labels_reject_scalar_and_money_noise(self) -> None:
        lines = _build_lines([
            _word("Account", 20, 10),
            _word("12345", 80, 10),
            _word("Entity", 20, 28),
            _word("Value", 74, 28),
            _word("USD", 310, 28),
            _word("10.00", 338, 28),
        ])

        self.assertIsNone(_party_field(lines, "buyer"))
        self.assertIsNone(_party_field(lines, "seller"))

    def test_total_money_field_uses_word_geometry_for_amount_box(self) -> None:
        lines = _build_lines([
            _word("Balance", 40, 10),
            _word("Due", 78, 10),
            _word("approved", 130, 10),
            _word("USD", 310, 10),
            _word("216.00", 338, 10),
        ])

        field = _best_money_field(lines, "balance_due", "USD")

        self.assertIsNotNone(field)
        assert field is not None
        self.assertEqual(field["amount"], 216.0)
        self.assertEqual(field["raw"], "USD 216.00")
        self.assertGreaterEqual(field["bbox"][0], 300)

    def test_currency_inference_uses_rmb_before_ambiguous_yen_glyph(self) -> None:
        lines = _build_lines([
            _word("Item", 40, 10),
            _word("RMB", 300, 10),
            _word("Amt", 326, 10),
            _word("Support", 40, 26),
            _word("Y", 310, 26),
            _word("900", 326, 26),
        ])

        currency = _infer_currency(lines)

        self.assertIsNotNone(currency)
        assert currency is not None
        self.assertEqual(currency["value"], "CNY")


class WordGeometryTableParserTests(unittest.TestCase):
    def test_column_inference_from_header_word_boxes(self) -> None:
        words = [
            _word("Item", 40, 10),
            _word("Qty", 180, 10),
            _word("Rate", 240, 10),
            _word("Amount", 330, 10),
            _word("Service", 40, 26),
            _word("2", 194, 26),
            _word("USD", 252, 26),
            _word("100.00", 276, 26),
            _word("USD", 350, 26),
            _word("200.00", 374, 26),
        ]
        lines = _build_lines(words)

        header = _infer_table_header(lines[0], lines)

        self.assertIsNotNone(header)
        assert header is not None
        self.assertEqual([column.role for column in header.columns], [
            "description",
            "quantity",
            "unit_price",
            "amount",
        ])
        self.assertLess(header.columns[-1].range_x0, 350)
        self.assertGreater(header.columns[-1].range_x1, 390)

    def test_ambiguous_amount_header_labels_resolve_to_final_currency_column(self) -> None:
        words = [
            _word("Line", 40, 10),
            _word("Charge", 118, 10),
            _word("Net", 210, 10),
            _word("Taxable", 275, 10),
            _word("JPY", 350, 10),
            _word("Support", 40, 26),
            _word("1,000", 350, 26),
        ]
        lines = _build_lines(words)

        header = _infer_table_header(lines[0], lines)

        self.assertIsNotNone(header)
        assert header is not None
        self.assertEqual(header.columns[0].role, "description")
        self.assertEqual(header.columns[-1].role, "amount")
        self.assertEqual(header.columns[-1].label, "jpy")

    def test_row_grouping_by_y_position(self) -> None:
        groups = _group_words_by_y([
            _word("A", 10, 10),
            _word("B", 30, 10.7),
            _word("C", 10, 30),
        ])

        self.assertEqual([[word.text for word in group] for group in groups], [["A", "B"], ["C"]])

    def test_wrapped_description_continuation_merges_into_previous_row(self) -> None:
        words = [
            _word("Applied", 20, 10),
            _word("Ref", 70, 10),
            _word("Concept", 120, 10),
            _word("Units", 250, 10),
            _word("Each", 285, 10),
            _word("Importe", 335, 10),
            _word("May", 20, 26),
            _word("31", 42, 26),
            _word("2026", 60, 26),
            _word("LN-1", 70, 26),
            _word("Preventive", 120, 26),
            _word("maintenance", 165, 26),
            _word("6", 264, 26),
            _word("Mex$", 288, 26),
            _word("484,91", 310, 26),
            _word("Mex$", 338, 26),
            _word("2.909,46", 365, 26),
            _word("calibration,", 120, 36),
            _word("and", 170, 36),
            _word("test", 188, 36),
            _word("Subtotal", 20, 54),
            _word("Mex$", 338, 54),
            _word("2.909,46", 365, 54),
        ]

        items = _line_items_from_word_tables(words, _build_lines(words), "MXN")

        self.assertEqual(len(items), 1)
        self.assertIn("calibration", items[0]["description"]["raw"])
        self.assertEqual(items[0]["quantity"]["value"], 6)
        self.assertEqual(items[0]["unit_price"]["amount"], 484.91)
        self.assertEqual(items[0]["amount"]["amount"], 2909.46)

    def test_quantity_rate_and_amount_assignment_uses_cells(self) -> None:
        words = [
            _word("Item", 40, 10),
            _word("Qty", 180, 10),
            _word("Rate", 240, 10),
            _word("Amount", 330, 10),
            _word("Platform", 40, 26),
            _word("license", 82, 26),
            _word("2", 194, 26),
            _word("USD", 252, 26),
            _word("100.00", 276, 26),
            _word("USD", 350, 26),
            _word("200.00", 374, 26),
        ]

        items = _line_items_from_word_tables(words, _build_lines(words), "USD")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["quantity"]["value"], 2)
        self.assertEqual(items[0]["unit_price"]["amount"], 100.0)
        self.assertEqual(items[0]["amount"]["amount"], 200.0)
        self.assertIsInstance(items[0]["amount"]["bbox"], list)

    def test_totals_row_stops_word_table_parsing(self) -> None:
        words = [
            _word("Item", 40, 10),
            _word("Qty", 180, 10),
            _word("Rate", 240, 10),
            _word("Amount", 330, 10),
            _word("Platform", 40, 26),
            _word("2", 194, 26),
            _word("USD", 252, 26),
            _word("100.00", 276, 26),
            _word("USD", 350, 26),
            _word("200.00", 374, 26),
            _word("Balance", 40, 44),
            _word("Due", 76, 44),
            _word("2", 194, 44),
            _word("USD", 350, 44),
            _word("216.00", 374, 44),
            _word("Should", 40, 62),
            _word("skip", 80, 62),
            _word("1", 194, 62),
            _word("USD", 252, 62),
            _word("1.00", 276, 62),
            _word("USD", 350, 62),
            _word("1.00", 374, 62),
        ]

        items = _line_items_from_word_tables(words, _build_lines(words), "USD")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"]["raw"], "Platform")

    def test_valid_item_tables_are_combined_across_pages(self) -> None:
        words = [
            _word("Item", 40, 10),
            _word("Qty", 180, 10),
            _word("Rate", 240, 10),
            _word("Amount", 330, 10),
            _word("Platform", 40, 26),
            _word("2", 194, 26),
            _word("USD", 252, 26),
            _word("100.00", 276, 26),
            _word("USD", 350, 26),
            _word("200.00", 374, 26),
            _word("Item", 40, 10, page=2),
            _word("Qty", 180, 10, page=2),
            _word("Rate", 240, 10, page=2),
            _word("Amount", 330, 10, page=2),
            _word("Implementation", 40, 26, page=2),
            _word("3", 194, 26, page=2),
            _word("USD", 252, 26, page=2),
            _word("150.00", 276, 26, page=2),
            _word("USD", 350, 26, page=2),
            _word("450.00", 374, 26, page=2),
        ]

        items = _line_items_from_word_tables(words, _build_lines(words), "USD")

        self.assertEqual([item["raw"] for item in items], ["Platform", "Implementation"])
        self.assertEqual([item["amount"]["amount"] for item in items], [200.0, 450.0])

    def test_table_continuation_page_without_header_uses_previous_header_shape(self) -> None:
        words = [
            _word("Item", 40, 10),
            _word("Qty", 180, 10),
            _word("Rate", 240, 10),
            _word("Amount", 330, 10),
            _word("Platform", 40, 26),
            _word("2", 194, 26),
            _word("USD", 252, 26),
            _word("100.00", 276, 26),
            _word("USD", 350, 26),
            _word("200.00", 374, 26),
            _word("Implementation", 40, 44, page=2),
            _word("3", 194, 44, page=2),
            _word("USD", 252, 44, page=2),
            _word("150.00", 276, 44, page=2),
            _word("USD", 350, 44, page=2),
            _word("450.00", 374, 44, page=2),
        ]

        items = _line_items_from_word_tables(words, _build_lines(words), "USD")

        self.assertEqual([item["raw"] for item in items], ["Platform", "Implementation"])

    def test_note_footer_rows_inside_table_bounds_stop_item_parsing(self) -> None:
        words = [
            _word("Item", 40, 10),
            _word("Qty", 180, 10),
            _word("Rate", 240, 10),
            _word("Amount", 330, 10),
            _word("Platform", 40, 26),
            _word("2", 194, 26),
            _word("USD", 252, 26),
            _word("100.00", 276, 26),
            _word("USD", 350, 26),
            _word("200.00", 374, 26),
            _word("Notes", 40, 44),
            _word("USD", 350, 44),
            _word("5.00", 374, 44),
            _word("Should", 40, 62),
            _word("skip", 82, 62),
            _word("USD", 350, 62),
            _word("1.00", 374, 62),
        ]

        items = _line_items_from_word_tables(words, _build_lines(words), "USD")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["raw"], "Platform")


@unittest.skipUnless(HAS_PDFPLUMBER, "pdfplumber is required for parser integration tests")
class InvoiceParserTests(unittest.TestCase):
    def test_parse_invoice_pdf_extracts_core_fields_with_evidence(self) -> None:
        result = parse_invoice_pdf(_simple_invoice_pdf(), source_id="fixture:manual")
        fields = result["fields"]

        self.assertEqual(result["status"], "parsed")
        self.assertEqual(fields["invoice_number"]["value"], "INV-2026-0042")
        self.assertEqual(fields["issue_date"]["value"], "2026-06-10")
        self.assertEqual(fields["due_date"]["value"], "2026-07-10")
        self.assertEqual(fields["purchase_order"]["value"], "PO-7788")
        self.assertEqual(fields["terms"]["value"], "Net 30")
        self.assertEqual(fields["balance_due"]["amount"], 216.0)
        self.assertEqual(fields["balance_due"]["currency"], "USD")
        self.assertIsInstance(fields["invoice_number"]["bbox"], list)
        self.assertIn("Acme Supplies", fields["seller"]["raw"])
        self.assertIn("Beta Foods", fields["buyer"]["raw"])
        self.assertEqual(fields["line_items"][0]["quantity"]["value"], 2)
        self.assertEqual(fields["line_items"][0]["amount"]["amount"], 200.0)

    def test_consignor_consignee_labels_parse_as_seller_and_buyer(self) -> None:
        result = parse_invoice_pdf(_consignor_consignee_invoice_pdf(), source_id="fixture:consignment")
        fields = result["fields"]

        self.assertEqual(fields["seller"]["label"], "consignor")
        self.assertIn("Alpha Logistics LLC", fields["seller"]["raw"])
        self.assertIn("10 Port Road", fields["seller"]["raw"])
        self.assertNotIn("Beta Foods", fields["seller"]["raw"])
        self.assertEqual(fields["buyer"]["label"], "consignee")
        self.assertIn("Beta Foods Inc", fields["buyer"]["raw"])
        self.assertIn("88 Market Lane", fields["buyer"]["raw"])
        self.assertNotIn("Invoice No.", fields["buyer"]["raw"])

    def test_parse_invoice_pdf_returns_no_text_layer_without_ocr(self) -> None:
        canvas = _PdfCanvas(
            width_pt=210 * MM_TO_PT,
            height_pt=297 * MM_TO_PT,
            font_style="system",
        )
        canvas.rect(10, 10, 30, 20, fill="#ffffff", stroke="#111827")

        result = parse_invoice_pdf(_build_pdf([canvas]))

        self.assertEqual(result["status"], "no_text_layer")
        self.assertEqual(result["fields"]["line_items"], [])
        self.assertIn("OCR is not attempted", result["warnings"][0])

    def test_generated_invoice_fixture_parses_required_acceptance_fields(self) -> None:
        sample = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=1234,
            today=date(2026, 7, 1),
        )

        result = parse_invoice_pdf(render_invoice_pdf([sample]))
        fields = result["fields"]

        self.assertEqual(result["status"], "parsed")
        self.assertEqual(fields["invoice_number"]["value"], sample["data"]["invoice_number"])
        self.assertIsNotNone(fields["issue_date"])
        self.assertIsNotNone(fields["balance_due"])
        self.assertTrue(fields["seller"] or fields["buyer"] or fields["line_items"])

    def test_partial_po_context_is_inferred_not_rendered_into_invoice_text(self) -> None:
        sample = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4-half-horizontal",
            seed=123,
            variation_index=1,
            today=date(2026, 7, 7),
        )
        ap_context = sample["data"]["ap_context"]
        context = ap_context["context"]
        expected = ap_context["expected"]
        previous_document = context["previous_related_document"]
        client_document = _previous_related_document(
            context["client_database"],
            context["po_number"],
        )
        vendor_document = _previous_related_document(
            context["vendor_database"],
            context["po_number"],
        )

        result = parse_invoice_pdf(render_invoice_pdf([sample]), source_id="fixture:partial-po")
        fields = result["fields"]
        visible_text = "\n".join(str(page.get("text") or "") for page in result["pages"])

        self.assertEqual(result["status"], "parsed")
        self.assertEqual(fields["purchase_order"]["value"], context["po_number"])
        self.assertEqual(fields["balance_due"]["amount"], float(context["invoice_total"]))
        self.assertEqual(client_document["invoice_number"], previous_document["invoice_number"])
        self.assertEqual(vendor_document["invoice_number"], previous_document["invoice_number"])
        self.assertEqual(client_document["applied_to_po"], context["po_previously_consumed"])
        self.assertEqual(vendor_document["applied_to_po"], context["po_previously_consumed"])
        self.assertEqual(client_document["vendor_name"], vendor_document["vendor_name"])
        self.assertEqual(client_document["buyer_name"], vendor_document["buyer_name"])
        self.assertEqual(previous_document["applied_to_po"], context["po_previously_consumed"])
        self.assertTrue(expected["requires_client_vendor_match"])
        for fragment in _forbidden_visible_ap_fragments(ap_context):
            self.assertNotIn(fragment, visible_text)

    def test_default_partial_po_corpus_does_not_render_ap_ground_truth(self) -> None:
        partial_count = 0
        for entry in iter_invoice_corpus(seed=1000, today=date(2026, 7, 10)):
            if entry.suite != "standard":
                break
            sample = entry.samples[0]
            ap_context = sample["data"].get("ap_context", {})
            if ap_context.get("scenario") != "split_po_partial_billing":
                continue

            partial_count += 1
            with self.subTest(pdf=entry.pdf_filename):
                result = parse_invoice_pdf(
                    render_invoice_pdf(entry.samples),
                    source_id=entry.pdf_filename,
                )
                visible_text = "\n".join(str(page.get("text") or "") for page in result["pages"])

                self.assertEqual(result["status"], "parsed")
                for fragment in _forbidden_visible_ap_fragments(ap_context):
                    self.assertNotIn(fragment, visible_text)

        self.assertGreater(partial_count, 0)

    @unittest.skipUnless(SAMPLE_0001_A4.exists(), "generated invoice-sample-0001-a4-ledger-clean.pdf fixture is missing")
    def test_generated_a4_fixture_extracts_word_geometry_line_items(self) -> None:
        content = SAMPLE_0001_A4.read_bytes()

        result = parse_invoice_pdf(content)
        fields = result["fields"]
        line_items = result["fields"]["line_items"]
        parsed_boxes, _ = highlight_boxes_for_mode(content, result, box_mode="parsed")

        self.assertEqual(len(line_items), 6)
        first_item = line_items[0]
        self.assertEqual(first_item["raw"], "Support retainer - Reserved engineering response hours")
        self.assertIn("$928.90", first_item["row_raw"])
        self.assertEqual(first_item["value"]["description"], first_item["description"]["value"])
        self.assertEqual(first_item["value"]["quantity"], first_item["quantity"]["value"])
        self.assertEqual(first_item["value"]["unit_price"], first_item["unit_price"]["amount"])
        self.assertEqual(first_item["value"]["amount"], first_item["amount"]["amount"])
        _assert_manifest_parties(self, SAMPLE_0001_A4, fields)
        for item in line_items:
            self.assertIsInstance(item["bbox"], list)
            self.assertIsInstance(item["amount"]["bbox"], list)
        self.assertGreaterEqual(len(parsed_boxes), 35)

    @unittest.skipUnless(SAMPLE_0002_HALF.exists(), "generated invoice-sample-0002 half-page fixture is missing")
    def test_side_by_side_party_columns_parse_as_separate_multiline_blocks(self) -> None:
        result = parse_invoice_pdf(SAMPLE_0002_HALF.read_bytes())
        fields = result["fields"]

        _assert_manifest_parties(self, SAMPLE_0002_HALF, fields)

    @unittest.skipUnless(SAMPLE_0005_HALF.exists(), "generated invoice-sample-0005 half-page fixture is missing")
    def test_destination_header_wins_over_account_ref_in_row_layout(self) -> None:
        result = parse_invoice_pdf(SAMPLE_0005_HALF.read_bytes())
        fields = result["fields"]

        _assert_manifest_parties(self, SAMPLE_0005_HALF, fields)

    @unittest.skipUnless(SAMPLE_0006_THIRD.exists(), "generated invoice-sample-0006 third-page fixture is missing")
    def test_payee_requester_row_layout_still_parses_both_party_blocks(self) -> None:
        result = parse_invoice_pdf(SAMPLE_0006_THIRD.read_bytes())
        fields = result["fields"]

        _assert_manifest_parties(self, SAMPLE_0006_THIRD, fields)


if __name__ == "__main__":
    unittest.main()
