from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.invoice_generator import (
    AP_EDGE_CASE_SCENARIOS,
    BASE_TEMPLATES,
    PAPER_FORMATS,
    _is_decorative_component,
    _resolve_non_decorative_overlaps,
    generate_invoice,
    generate_invoice_samples,
)
from app.templates import invoice_samples_page

DECORATIVE_COMPONENTS = {"accent-band", "accent-rail", "watermark"}


class InvoiceGeneratorTests(unittest.TestCase):
    def test_base_templates_are_unique_and_complete(self) -> None:
        slugs = [template.slug for template in BASE_TEMPLATES]

        self.assertEqual(len(BASE_TEMPLATES), 15)
        self.assertEqual(len(set(slugs)), 15)
        self.assertTrue(all(template.layout_family for template in BASE_TEMPLATES))
        self.assertTrue(all(template.logo_shape for template in BASE_TEMPLATES))
        self.assertGreaterEqual(len({template.header_style for template in BASE_TEMPLATES}), 8)

    def test_paper_formats_include_a4_horizontal_splits(self) -> None:
        papers = {paper.slug: paper for paper in PAPER_FORMATS}

        self.assertEqual(papers["a4"].width_mm, 210)
        self.assertEqual(papers["a4"].height_mm, 297)
        self.assertEqual(papers["a4-half-horizontal"].width_mm, 210)
        self.assertEqual(papers["a4-half-horizontal"].height_mm, 148.5)
        self.assertEqual(papers["a4-third-horizontal"].width_mm, 210)
        self.assertEqual(papers["a4-third-horizontal"].height_mm, 99)

    def test_full_a4_invoice_has_core_components_and_data(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            today=date(2026, 7, 7),
        )
        kinds = {component["kind"] for component in invoice["components"]}

        self.assertEqual(invoice["paper"]["slug"], "a4")
        self.assertRegex(invoice["data"]["invoice_number"], r"^[A-Z]{2,4}-2026\d{2}-\d{4}$")
        self.assertLess(invoice["data"]["issue_date"], invoice["data"]["due_date"])
        self.assertGreaterEqual(len(invoice["data"]["items"]), 3)
        self.assertTrue(
            {
                "company-header",
                "logo",
                "title",
                "seller",
                "buyer",
                "invoice-meta",
                "dates",
                "items-table",
            }.issubset(kinds)
        )
        self.assertTrue("totals" in kinds or invoice["data"]["table"]["total_in_table"])
        company_header = next(
            component for component in invoice["components"] if component["kind"] == "company-header"
        )
        self.assertEqual(company_header["x_mm"], 0)
        self.assertEqual(company_header["width_mm"], 210)
        self.assertEqual(company_header["variant"], "split")
        self.assertEqual(invoice["layout_score"]["overflow_mm"], 0)

    def test_default_samples_cover_all_15_base_templates(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4",
            count=15,
            seed=500,
            today=date(2026, 7, 7),
        )

        self.assertEqual(len(samples), 15)
        self.assertEqual(
            {sample["template"]["slug"] for sample in samples},
            {template.slug for template in BASE_TEMPLATES},
        )
        header_variants = {
            next(component for component in sample["components"] if component["kind"] == "company-header")[
                "variant"
            ]
            for sample in samples
        }
        font_styles = {sample["template"]["font_style"] for sample in samples}
        self.assertGreaterEqual(len(header_variants), 8)
        self.assertIn("centered", header_variants)
        self.assertIn("minimal-no-line", header_variants)
        self.assertEqual(len(font_styles), 15)
        self.assertIn("serif", font_styles)
        self.assertIn("mono", font_styles)
        self.assertIn("condensed", font_styles)

    def test_default_samples_cover_capture_format_variations(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4",
            count=15,
            seed=500,
            today=date(2026, 7, 7),
        )
        date_patterns = {sample["data"]["formatting"]["date_pattern"] for sample in samples}
        currencies = {sample["data"]["currency"] for sample in samples}
        invoice_number_styles = {sample["data"]["invoice_number_style"] for sample in samples}
        table_variants = {sample["data"]["table"]["variant"] for sample in samples}
        balance_labels = {sample["data"]["labels"]["balance_due"] for sample in samples}
        money_styles = {sample["data"]["formatting"]["money_style"] for sample in samples}

        self.assertIn("DDMMYYYY", date_patterns)
        self.assertIn("DDMMYY", date_patterns)
        self.assertIn("MMDDYYYY", date_patterns)
        self.assertIn("INR", currencies)
        self.assertIn("USD", currencies)
        self.assertIn("EUR", currencies)
        self.assertGreaterEqual(len(currencies), 8)
        self.assertGreaterEqual(len(invoice_number_styles), 12)
        self.assertGreaterEqual(len(table_variants), 12)
        self.assertIn("Left Balance", balance_labels)
        self.assertIn("Remaining Payment", balance_labels)
        self.assertTrue(any("0dp" in style for style in money_styles))
        self.assertTrue(any(style.startswith("symbol-") for style in money_styles))

    def test_display_fields_can_differ_from_normalized_values(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            today=date(2026, 7, 7),
        )

        self.assertNotEqual(invoice["data"]["issue_date"], invoice["data"]["issue_date_display"])
        self.assertEqual(invoice["data"]["formatting"]["date_pattern"], "DDMMYYYY")
        self.assertRegex(invoice["data"]["issue_date_display"], r"^\d{8}$")

    def test_financial_totals_round_each_intermediate_amount(self) -> None:
        for seed in range(120, 145):
            invoice = generate_invoice(
                template_slug=BASE_TEMPLATES[seed % len(BASE_TEMPLATES)].slug,
                paper_slug="a4",
                seed=seed,
                variation_index=seed % 15,
                today=date(2026, 7, 7),
            )
            data = invoice["data"]
            subtotal = _money_round(sum(_money_decimal(item["amount"]) for item in data["items"]))
            discount = _money_decimal(data["discount"])
            taxable = _money_round(subtotal - discount)
            tax = _money_round(taxable * _money_decimal(data["tax_rate"]))
            total = _money_round(taxable + tax + _money_decimal(data["shipping"]))
            balance_due = _money_round(total - _money_decimal(data["paid"]))

            self.assertEqual(_money_decimal(data["subtotal"]), subtotal)
            self.assertEqual(_money_decimal(data["tax"]), tax)
            self.assertEqual(_money_decimal(data["total"]), total)
            self.assertEqual(_money_decimal(data["balance_due"]), balance_due)

    def test_generated_components_do_not_overlap(self) -> None:
        for paper in PAPER_FORMATS:
            samples = generate_invoice_samples(
                paper_slug=paper.slug,
                count=15,
                seed=777,
                today=date(2026, 7, 7),
            )
            for sample in samples:
                components = [
                    component
                    for component in sample["components"]
                    if component["kind"] not in DECORATIVE_COMPONENTS
                ]
                for index, first in enumerate(components):
                    for second in components[index + 1 :]:
                        self.assertFalse(
                            _rectangles_overlap(first, second),
                            f"{sample['id']} has overlapping {first['kind']} and {second['kind']}",
                        )
                self.assertEqual(sample["layout_score"]["overflow_mm"], 0)

    def test_overlap_resolver_handles_dense_collision_stack(self) -> None:
        components = [
            {
                "kind": f"block-{index}",
                "x_mm": 10,
                "y_mm": 10,
                "width_mm": 40,
                "height_mm": 8,
                "priority": 1,
                "optional": False,
            }
            for index in range(220)
        ]

        resolved = _resolve_non_decorative_overlaps(PAPER_FORMATS[0], components)

        self.assertEqual(
            [component["kind"] for component in resolved],
            [component["kind"] for component in components],
        )
        for index, first in enumerate(resolved):
            for second in resolved[index + 1 :]:
                self.assertFalse(_rectangles_overlap(first, second))

    def test_a4_can_place_total_inside_items_table(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            today=date(2026, 7, 7),
        )
        kinds = {component["kind"] for component in invoice["components"]}

        self.assertTrue(invoice["data"]["table"]["total_in_table"])
        self.assertIn("items-table", kinds)
        self.assertNotIn("totals", kinds)

    def test_table_total_row_renders_blank_cells_and_symbol_currency(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            today=date(2026, 7, 7),
        )
        html = invoice_samples_page(
            samples=[invoice],
            papers=[],
            templates=[],
            active_paper="a4",
            active_template="ledger-clean",
            seed=123,
            count=1,
        )

        self.assertIn("invoice-table-total-row", html)
        self.assertIn("<td class=\"number invoice-col-unit_price\"></td>", html)
        self.assertIn("$", html)

    def test_table_amount_boundary_collision_renders_print_drift_markup(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            variation_index=1,
            today=date(2026, 7, 7),
        )
        html = invoice_samples_page(
            samples=[invoice],
            papers=[],
            templates=[],
            active_paper="a4",
            active_template="ledger-clean",
            seed=123,
            count=1,
        )

        self.assertEqual(invoice["data"]["table"]["visual_density"], "amount_boundary_collision")
        self.assertIn("table-density-amount_boundary_collision", html)
        self.assertIn("invoice-print-drift", html)
        self.assertEqual(
            invoice["data"]["visual_artifacts"][-1]["scenario"],
            "table_amount_boundary_collision",
        )

    def test_terms_and_footer_use_distinct_generated_copy(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=500,
            today=date(2026, 7, 7),
        )
        kinds = {component["kind"] for component in invoice["components"]}
        html = invoice_samples_page(
            samples=[invoice],
            papers=[],
            templates=[],
            active_paper="a4",
            active_template="ledger-clean",
            seed=500,
            count=1,
        )

        self.assertIn("terms", kinds)
        self.assertIn("footer", kinds)
        self.assertNotEqual(invoice["data"]["notes"], invoice["data"]["footer_note"])
        self.assertFalse(
            invoice["data"]["footer_note"].startswith(f"{invoice['template']['industry']} ")
        )
        self.assertIn(invoice["data"]["notes"], html)
        self.assertIn(invoice["data"]["footer_note"], html)
        self.assertIn("<h3>Notice</h3>", html)

    def test_footer_without_footer_note_does_not_repeat_terms_copy(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=500,
            today=date(2026, 7, 7),
        )
        invoice["data"]["notes"] = "Terms copy should appear once."
        invoice["data"].pop("footer_note", None)
        html = invoice_samples_page(
            samples=[invoice],
            papers=[],
            templates=[],
            active_paper="a4",
            active_template="ledger-clean",
            seed=500,
            count=1,
        )

        self.assertEqual(html.count("Terms copy should appear once."), 1)

    def test_table_total_label_uses_descriptive_column(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            variation_index=22,
            today=date(2026, 7, 7),
        )
        html = invoice_samples_page(
            samples=[invoice],
            papers=[],
            templates=[],
            active_paper="a4",
            active_template="ledger-clean",
            seed=123,
            count=1,
        )

        self.assertIn("schema-receipt-lines", html)
        self.assertIn("<td class=\"number invoice-col-line\"></td>", html)
        self.assertIn('<td class="invoice-col-item_plain"><strong>AMOUNT LEFT</strong>', html)

    def test_default_samples_include_credit_memo_edge_case(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4",
            count=15,
            seed=500,
            today=date(2026, 7, 7),
        )

        scenarios = {
            sample["data"].get("ap_context", {}).get("scenario", "none")
            for sample in samples
        }

        self.assertIn("credit_memo_negative_balance", scenarios)

    def test_count_60_covers_all_ap_edge_case_scenarios(self) -> None:
        samples = generate_invoice_samples(
            paper_slug="a4",
            count=60,
            seed=500,
            today=date(2026, 7, 7),
        )

        scenarios = {
            sample["data"].get("ap_context", {}).get("scenario", "none")
            for sample in samples
        }

        self.assertTrue(set(AP_EDGE_CASE_SCENARIOS).issubset(scenarios))

    def test_credit_memo_has_negative_lines_and_apply_credit_decision(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            variation_index=7,
            today=date(2026, 7, 7),
        )
        data = invoice["data"]
        ap_context = data["ap_context"]

        self.assertEqual(data["labels"]["document_title"], "Credit Memo")
        self.assertTrue(data["invoice_number"].startswith("CM-"))
        self.assertLess(_money_decimal(data["subtotal"]), 0)
        self.assertLess(_money_decimal(data["balance_due"]), 0)
        self.assertEqual(ap_context["scenario"], "credit_memo_negative_balance")
        self.assertEqual(ap_context["expected"]["decision"], "apply_credit_or_route_review")
        self.assertTrue(all(_money_decimal(item["amount"]) < 0 for item in data["items"]))
        for item in data["items"]:
            row_total = _money_round(_money_decimal(item["quantity"]) * _money_decimal(item["unit_price"]))
            self.assertEqual(row_total, _money_decimal(item["amount"]))

    def test_invoice_number_occlusion_stamp_is_decorative_overlap(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            variation_index=17,
            today=date(2026, 7, 7),
        )
        invoice_meta = next(
            component for component in invoice["components"] if component["kind"] == "invoice-meta"
        )
        occlusion_stamp = next(
            component
            for component in invoice["components"]
            if component["kind"] == "stamp" and component.get("variant") == "invoice-number-occlusion"
        )

        self.assertTrue(_rectangles_overlap(invoice_meta, occlusion_stamp))
        self.assertTrue(_is_decorative_component(occlusion_stamp))
        self.assertEqual(
            invoice["data"]["visual_artifacts"][-1]["scenario"],
            "invoice_number_seal_occlusion",
        )

    def test_horizontal_split_formats_trim_rows_and_optional_components(self) -> None:
        full = generate_invoice(
            template_slug="market-slip",
            paper_slug="a4",
            seed=900,
            today=date(2026, 7, 7),
        )
        third = generate_invoice(
            template_slug="market-slip",
            paper_slug="a4-third-horizontal",
            seed=900,
            today=date(2026, 7, 7),
        )
        third_kinds = {component["kind"] for component in third["components"]}
        company_header = next(
            component for component in third["components"] if component["kind"] == "company-header"
        )

        self.assertEqual(third["paper"]["width_mm"], 210)
        self.assertEqual(third["paper"]["height_mm"], 99)
        self.assertEqual(company_header["width_mm"], 210)
        self.assertGreaterEqual(company_header["height_mm"], 10)
        self.assertLessEqual(len(third["data"]["items"]), len(full["data"]["items"]))
        self.assertLessEqual(len(third["data"]["items"]), 3)
        self.assertIn("payment", third_kinds)
        self.assertNotIn("barcode", third_kinds)
        self.assertEqual(third["layout_score"]["overflow_mm"], 0)

    def test_horizontal_splits_use_separate_totals_without_table_summary_row(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4-third-horizontal",
            seed=123,
            today=date(2026, 7, 7),
        )
        kinds = {component["kind"] for component in invoice["components"]}
        html = invoice_samples_page(
            samples=[invoice],
            papers=[],
            templates=[],
            active_paper="a4-third-horizontal",
            active_template="ledger-clean",
            seed=123,
            count=1,
        )

        self.assertFalse(invoice["data"]["table"]["total_in_table"])
        self.assertIn("totals", kinds)
        self.assertNotIn("invoice-table-total-row", html)

    def test_a4_third_dates_are_limited_to_two_rows(self) -> None:
        invoice = generate_invoice(
            template_slug="studio-block",
            paper_slug="a4-third-horizontal",
            seed=42,
            today=date(2026, 7, 7),
        )
        dates = next(component for component in invoice["components"] if component["kind"] == "dates")

        self.assertEqual(dates["variant"], "two-row")
        self.assertLessEqual(dates["height_mm"], 7)

    def test_old_vertical_slugs_alias_to_horizontal_splits(self) -> None:
        invoice = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4-half-vertical",
            seed=42,
            today=date(2026, 7, 7),
        )

        self.assertEqual(invoice["paper"]["slug"], "a4-half-horizontal")
        self.assertEqual(invoice["paper"]["width_mm"], 210)
        self.assertEqual(invoice["paper"]["height_mm"], 148.5)

    def test_horizontal_half_header_is_large_enough_for_letterhead(self) -> None:
        invoice = generate_invoice(
            template_slug="studio-block",
            paper_slug="a4-half-horizontal",
            seed=42,
            today=date(2026, 7, 7),
        )
        company_header = next(
            component for component in invoice["components"] if component["kind"] == "company-header"
        )

        self.assertEqual(company_header["variant"], "centered")
        self.assertGreaterEqual(company_header["height_mm"], 18)
        self.assertEqual(invoice["layout_score"]["overflow_mm"], 0)

    def test_seed_and_anchor_date_make_generation_deterministic(self) -> None:
        first = generate_invoice(
            template_slug="signal-card",
            paper_slug="a4-half-horizontal",
            seed=42,
            today=date(2026, 7, 7),
        )
        second = generate_invoice(
            template_slug="signal-card",
            paper_slug="a4-half-horizontal",
            seed=42,
            today=date(2026, 7, 7),
        )

        self.assertEqual(first, second)


def _rectangles_overlap(first: dict[str, object], second: dict[str, object]) -> bool:
    return not (
        float(first["x_mm"]) + float(first["width_mm"]) <= float(second["x_mm"])
        or float(second["x_mm"]) + float(second["width_mm"]) <= float(first["x_mm"])
        or float(first["y_mm"]) + float(first["height_mm"]) <= float(second["y_mm"])
        or float(second["y_mm"]) + float(second["height_mm"]) <= float(first["y_mm"])
    )


def _money_decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _money_round(value: object) -> Decimal:
    return _money_decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


if __name__ == "__main__":
    unittest.main()
