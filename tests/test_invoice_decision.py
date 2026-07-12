from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path
from typing import Any

from app.ap_context import (
    iter_ap_context_records_from_manifest,
    load_db_procurement_context,
    missing_procurement_context,
    procurement_context_from_manifest,
    load_procurement_context,
)
from app.invoice_decision import decide_invoice
from app.invoice_fixtures import build_invoice_manifest, write_invoice_manifest
from app.invoice_generator import generate_invoice
from app.invoice_normalizer import (
    normalize_invoice_number,
    normalize_invoice_parse,
    normalize_purchase_order,
    normalize_vendor_name,
)
from app.invoice_parser import parse_invoice_pdf
from app.invoice_pdf import render_invoice_pdf
from app.mail_store import MailRepository
from scripts.run_invoice_decision import run_invoice_decision


def _field(value: Any, *, confidence: float = 0.92, raw: Any | None = None) -> dict[str, Any]:
    return {
        "raw": value if raw is None else raw,
        "value": value,
        "page": 1,
        "bbox": [10.0, 20.0, 80.0, 28.0],
        "label": "test",
        "confidence": confidence,
        "method": "unit_test",
    }


def _money(value: Any, *, confidence: float = 0.94, currency: str = "USD") -> dict[str, Any]:
    return {
        "raw": f"{currency} {value}",
        "value": float(value),
        "amount": float(value),
        "currency": currency,
        "page": 1,
        "bbox": [120.0, 160.0, 180.0, 168.0],
        "label": "balance due",
        "confidence": confidence,
        "method": "unit_test",
    }


def _parsed_result(**field_overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "invoice_number": _field("INV-1045"),
        "issue_date": _field("2026-07-01"),
        "due_date": _field("2026-07-31"),
        "purchase_order": _field("PO-1000"),
        "terms": _field("Net 30"),
        "currency": _field("USD"),
        "seller": _field("Acme Supplies LLC\nbilling@acme.example"),
        "buyer": _field("Beta Foods Inc"),
        "subtotal": _money("1000.00"),
        "discount": _money("0.00"),
        "tax": _money("0.00"),
        "shipping": _money("0.00"),
        "paid": _money("0.00"),
        "balance_due": _money("1000.00"),
        "payment_instructions": _field("ACH transfer **** 1234\nbilling@acme.example"),
        "line_items": [
            {
                "value": {
                    "description": "Platform license",
                    "quantity": 1,
                    "unit_price": 1000.0,
                    "amount": 1000.0,
                    "currency": "USD",
                },
                "description": _field("Platform license"),
                "quantity": _field(1),
                "unit_price": _money("1000.00"),
                "amount": _money("1000.00"),
                "raw": "Platform license",
                "row_raw": "Platform license 1 USD 1000.00",
                "page": 1,
                "bbox": [10.0, 100.0, 180.0, 108.0],
                "confidence": 0.9,
                "method": "unit_test",
            }
        ],
    }
    fields.update(field_overrides)
    return {
        "status": "parsed",
        "parser_version": "unit-test",
        "fields": fields,
        "pages": [{"page": 1, "text": "invoice"}],
        "warnings": [],
    }


def _invoice_from_sample(sample: dict[str, Any]) -> dict[str, Any]:
    data = sample["data"]
    payment = data["payment"]
    fields = {
        "invoice_number": _field(data["invoice_number"]),
        "issue_date": _field(data["issue_date"]),
        "due_date": _field(data["due_date"]),
        "purchase_order": _field(data["purchase_order"]) if data["purchase_order"] else None,
        "terms": _field(data["terms"]),
        "currency": _field(data["currency"]),
        "seller": _field(f"{data['seller']['name']}\n{data['seller']['line1']}\n{data['seller']['email']}"),
        "buyer": _field(f"{data['buyer']['name']}\n{data['buyer']['line1']}"),
        "subtotal": _money(data["subtotal"], currency=data["currency"]),
        "discount": _money(data["discount"], currency=data["currency"]),
        "tax": _money(data["tax"], currency=data["currency"]),
        "shipping": _money(data["shipping"], currency=data["currency"]),
        "paid": _money(data["paid"], currency=data["currency"]),
        "balance_due": _money(data["balance_due"], currency=data["currency"]),
        "payment_instructions": _field(
            f"{payment['method']} {payment['account']}\n{payment['reference']}\n{payment['remit_to']}"
        ),
        "line_items": [
            {
                "value": {
                    "description": item["description"],
                    "quantity": item["quantity"],
                    "unit_price": item["unit_price"],
                    "amount": item["amount"],
                    "currency": data["currency"],
                },
                "description": _field(item["description"]),
                "quantity": _field(item["quantity"]),
                "unit_price": _money(item["unit_price"], currency=data["currency"]),
                "amount": _money(item["amount"], currency=data["currency"]),
                "raw": item["description"],
                "row_raw": f"{item['description']} {item['amount']}",
                "page": 1,
                "bbox": [10.0, 100.0, 180.0, 108.0],
                "confidence": 0.9,
                "method": "unit_test",
            }
            for item in data["items"]
        ],
    }
    return normalize_invoice_parse(
        {
            "status": "parsed",
            "parser_version": "unit-test",
            "fields": fields,
            "pages": [{"page": 1, "text": "invoice"}],
            "warnings": [],
        }
    )


def _matching_context(invoice: dict[str, Any]) -> dict[str, Any]:
    vendor = invoice["vendor"]
    buyer = invoice["buyer"]
    invoice_number = invoice["invoice_number"]
    issue_date = invoice["issue_date"]
    due_date = invoice["due_date"]
    purchase_order = invoice["purchase_order"]
    amount_due = invoice["amount_due"]
    amounts = invoice["amounts"]
    return {
        "schema_version": 1,
        "available": True,
        "source": {"type": "unit_test"},
        "scenario": "unit_approve",
        "vendor": {
            "name": vendor["name"],
            "normalized_name": vendor["normalized_name"],
            "aliases": [],
            "approved": True,
        },
        "buyer": {
            "name": buyer["name"],
            "normalized_name": buyer["normalized_name"],
        },
        "invoice": {
            "invoice_number": invoice_number["value"],
            "normalized_invoice_number": invoice_number["normalized"],
            "issue_date": issue_date["value"],
            "due_date": due_date["value"],
        },
        "currency": invoice["currency"]["value"],
        "purchase_order": {
            "po_number": purchase_order["value"],
            "normalized": purchase_order["normalized"],
            "authorized_total": amount_due["amount"],
            "previously_consumed": "0.00",
            "remaining_before_invoice": amount_due["amount"],
        },
        "invoice_total": amount_due["amount"],
        "approved_bank_details": {"account": "**** 1234"},
        "invoice_payment": {"bank_account": "**** 1234"},
        "previous_invoices": [],
        "duplicate_candidates": [],
        "candidate_open_po": None,
        "tolerance_policy": {"percent": "0.00", "amount": "0.00"},
        "amounts": {key: value["amount"] for key, value in amounts.items()},
        "expected": {},
    }


def _decision_for_variation(variation_index: int) -> dict[str, Any]:
    sample = generate_invoice(
        template_slug="ledger-clean",
        paper_slug="a4",
        seed=123,
        variation_index=variation_index,
        today=date(2026, 7, 7),
    )
    invoice = _invoice_from_sample(sample)
    manifest = build_invoice_manifest([sample], pdf_filename="invoice.pdf")
    context = procurement_context_from_manifest(manifest, invoice=invoice)
    return decide_invoice(invoice, context)


class InvoiceNormalizerTests(unittest.TestCase):
    def test_normalizes_clean_parsed_invoice(self) -> None:
        invoice = normalize_invoice_parse(_parsed_result())

        self.assertEqual(invoice["vendor"]["name"], "Acme Supplies LLC")
        self.assertEqual(invoice["vendor"]["normalized_name"], "acme supplies")
        self.assertEqual(invoice["invoice_number"]["normalized"], "1045")
        self.assertEqual(invoice["purchase_order"]["normalized"], "PO1000")
        self.assertEqual(invoice["amount_due"]["amount"], "1000.00")
        self.assertEqual(invoice["bank_details"]["bank_account"], "**** 1234")
        self.assertEqual(invoice["confidence"]["level"], "high")

    def test_tracks_missing_invoice_number_as_critical(self) -> None:
        invoice = normalize_invoice_parse(_parsed_result(invoice_number=None))

        self.assertIn("invoice_number", invoice["missing_fields"])
        self.assertIn("invoice_number", invoice["missing_critical_fields"])
        self.assertEqual(invoice["confidence"]["level"], "low")

    def test_tracks_missing_po_without_marking_it_critical(self) -> None:
        invoice = normalize_invoice_parse(_parsed_result(purchase_order=None))

        self.assertIn("purchase_order", invoice["missing_fields"])
        self.assertNotIn("purchase_order", invoice["missing_critical_fields"])

    def test_parser_required_identity_fields_are_critical_for_decisioning(self) -> None:
        invoice = normalize_invoice_parse(
            _parsed_result(due_date=None, currency=None, buyer=None)
        )

        self.assertTrue({"due_date", "currency", "buyer"}.issubset(invoice["missing_critical_fields"]))

    def test_tracks_low_confidence_fields(self) -> None:
        invoice = normalize_invoice_parse(
            _parsed_result(
                seller=_field("Acme Supplies LLC", confidence=0.45),
                issue_date=_field("2026-07-01", confidence=0.55),
                balance_due=_money("1000.00", confidence=0.60),
            )
        )

        self.assertIn("vendor", invoice["low_confidence_fields"])
        self.assertIn("issue_date", invoice["low_confidence_fields"])
        self.assertIn("amount_due", invoice["low_confidence_fields"])

    def test_no_text_layer_sets_missing_critical_fields(self) -> None:
        invoice = normalize_invoice_parse(
            {
                "status": "no_text_layer",
                "parser_version": "unit-test",
                "fields": {"line_items": []},
                "pages": [],
                "warnings": ["PDF has no usable text layer."],
            }
        )

        self.assertTrue(invoice["no_text_layer"])
        self.assertEqual(invoice["parser_status"], "no_text_layer")
        self.assertIn("vendor", invoice["missing_critical_fields"])


class InvoiceDecisionTests(unittest.TestCase):
    def test_no_procurement_context_routes_to_review(self) -> None:
        invoice = normalize_invoice_parse(_parsed_result())
        decision = decide_invoice(invoice, missing_procurement_context("No manifest."))

        self.assertEqual(decision["decision"], "needs_review")
        self.assertEqual(decision["confidence"], "low")

    def test_split_po_partial_billing_approves_partial_consumption(self) -> None:
        decision = _decision_for_variation(1)

        self.assertEqual(decision["decision"], "approve_partial_consumption")
        partial = next(check for check in decision["checks"] if check["id"] == "partial_po_consumption")
        self.assertEqual(partial["context"]["remaining_after_invoice"], "3000.00")

    def test_amount_variance_within_tolerance_approves_with_tolerance(self) -> None:
        decision = _decision_for_variation(2)

        self.assertEqual(decision["decision"], "approve_with_tolerance")
        amount = next(check for check in decision["checks"] if check["id"] == "amount_match")
        self.assertEqual(amount["status"], "pass_with_tolerance")

    def test_amount_variance_above_tolerance_routes_to_review(self) -> None:
        decision = _decision_for_variation(3)

        self.assertEqual(decision["decision"], "needs_review")
        amount = next(check for check in decision["checks"] if check["id"] == "amount_match")
        self.assertEqual(amount["status"], "fail")

    def test_duplicate_invoice_number_flags_possible_duplicate(self) -> None:
        decision = _decision_for_variation(4)

        self.assertEqual(decision["decision"], "flag_possible_duplicate")
        duplicate = next(check for check in decision["checks"] if check["id"] == "duplicate_invoice")
        self.assertEqual(duplicate["status"], "fail")

    def test_missing_po_with_implied_match_suggests_candidate_and_reviews(self) -> None:
        decision = _decision_for_variation(5)

        self.assertEqual(decision["decision"], "needs_review")
        self.assertIn("PO-IMPLIED-7421", decision["next_action"])

    def test_changed_bank_details_blocks_or_escalates(self) -> None:
        decision = _decision_for_variation(6)

        self.assertEqual(decision["decision"], "block_or_escalate")
        bank = next(check for check in decision["checks"] if check["id"] == "bank_details")
        self.assertEqual(bank["status"], "fail")

    def test_credit_memo_routes_to_credit_workflow(self) -> None:
        decision = _decision_for_variation(7)

        self.assertEqual(decision["decision"], "apply_credit_or_route_review")
        credit = next(check for check in decision["checks"] if check["id"] == "credit_memo")
        self.assertEqual(credit["status"], "review")

    def test_vendor_mismatch_blocks_partial_consumption_approval(self) -> None:
        invoice = normalize_invoice_parse(_parsed_result())
        context = _matching_context(invoice)
        context["vendor"] = {
            "name": "Wrong Vendor LLC",
            "normalized_name": "wrong vendor",
            "approved": True,
        }
        context["purchase_order"]["previously_consumed"] = "100.00"
        context["previous_invoices"] = [{"invoice_number": "INV-1000"}]

        decision = decide_invoice(invoice, context)

        self.assertEqual(decision["decision"], "needs_review")
        self.assertEqual(decision["checks"][-1]["id"], "vendor_match")
        self.assertEqual(decision["checks"][-1]["status"], "fail")

    def test_fuzzy_vendor_match_requires_review_but_approved_alias_passes(self) -> None:
        typo_invoice = normalize_invoice_parse(_parsed_result(seller=_field("Acme Supples LLC")))
        context = _matching_context(normalize_invoice_parse(_parsed_result()))

        typo_decision = decide_invoice(typo_invoice, context)

        self.assertEqual(typo_decision["decision"], "needs_review")
        fuzzy = typo_decision["checks"][-1]
        self.assertEqual(fuzzy["context"]["match_method"], "fuzzy_name")

        alias_context = _matching_context(typo_invoice)
        alias_context["vendor"] = {
            "name": "Acme Holdings LLC",
            "normalized_name": "acme holdings",
            "aliases": ["Acme Supples LLC"],
            "approved": True,
        }
        alias_decision = decide_invoice(typo_invoice, alias_context)
        self.assertEqual(alias_decision["decision"], "approve")

    def test_currency_mismatch_cannot_approve_equal_numeric_amount(self) -> None:
        invoice = normalize_invoice_parse(
            _parsed_result(
                currency=_field("EUR"),
                subtotal=_money("1000.00", currency="EUR"),
                discount=_money("0.00", currency="EUR"),
                tax=_money("0.00", currency="EUR"),
                shipping=_money("0.00", currency="EUR"),
                paid=_money("0.00", currency="EUR"),
                balance_due=_money("1000.00", currency="EUR"),
                line_items=[],
            )
        )
        context = _matching_context(invoice)
        context["currency"] = "USD"

        decision = decide_invoice(invoice, context)

        self.assertEqual(decision["decision"], "needs_review")
        currency = next(check for check in decision["checks"] if check["id"] == "currency_match")
        self.assertEqual(currency["status"], "fail")

    def test_bad_amount_composition_cannot_approve_matching_final_total(self) -> None:
        invoice = normalize_invoice_parse(
            _parsed_result(
                subtotal=_money("800.00"),
                tax=_money("100.00"),
                balance_due=_money("1000.00"),
                line_items=[],
            )
        )
        context = _matching_context(invoice)
        context["amounts"] = {}

        decision = decide_invoice(invoice, context)

        self.assertEqual(decision["decision"], "needs_review")
        composition = next(check for check in decision["checks"] if check["id"] == "amount_composition")
        self.assertEqual(composition["status"], "fail")
        self.assertEqual(composition["evidence"]["composition_variance"], "100.00")

    def test_parser_needs_review_and_low_confidence_amount_both_block_approval(self) -> None:
        parser_review = _parsed_result()
        parser_review["status"] = "needs_review"
        parser_review["warnings"] = ["Review required."]
        invoice = normalize_invoice_parse(parser_review)

        parser_decision = decide_invoice(invoice, _matching_context(invoice))

        self.assertEqual(parser_decision["decision"], "needs_review")
        self.assertEqual(parser_decision["checks"][-1]["id"], "parser_status")

        low_invoice = normalize_invoice_parse(_parsed_result(balance_due=_money("1000.00", confidence=0.40)))
        confidence_decision = decide_invoice(low_invoice, _matching_context(low_invoice))
        self.assertEqual(confidence_decision["decision"], "needs_review")
        self.assertEqual(confidence_decision["checks"][-2]["id"], "parse_confidence")

    def test_expected_invoice_number_dates_and_buyer_are_decisioned(self) -> None:
        invoice = normalize_invoice_parse(_parsed_result())

        cases = (
            ("buyer", {"name": "Other Buyer", "normalized_name": "other buyer"}, "buyer_match"),
            (
                "invoice",
                {
                    "invoice_number": "INV-9999",
                    "normalized_invoice_number": "9999",
                    "issue_date": "2026-07-01",
                    "due_date": "2026-07-31",
                },
                "invoice_number_match",
            ),
            (
                "invoice",
                {
                    "invoice_number": "INV-1045",
                    "normalized_invoice_number": "1045",
                    "issue_date": "2026-07-02",
                    "due_date": "2026-07-31",
                },
                "date_match",
            ),
        )
        for key, value, check_id in cases:
            with self.subTest(check_id=check_id):
                context = _matching_context(invoice)
                context[key] = value
                decision = decide_invoice(invoice, context)
                self.assertEqual(decision["decision"], "needs_review")
                check = next(item for item in decision["checks"] if item["id"] == check_id)
                self.assertEqual(check["status"], "fail")


class DbProcurementContextTests(unittest.TestCase):
    def test_manifest_context_can_seed_db_backed_ap_context_record(self) -> None:
        sample = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            variation_index=2,
            today=date(2026, 7, 7),
        )
        manifest = build_invoice_manifest([sample], pdf_filename="invoice.pdf")

        records = iter_ap_context_records_from_manifest(manifest, owner_user_id="user-123")

        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["owner_user_id"], "user-123")
        self.assertEqual(record["normalized_vendor"], normalize_vendor_name(sample["data"]["seller"]["name"]))
        self.assertEqual(record["normalized_purchase_order"], normalize_purchase_order(sample["data"]["purchase_order"]))
        self.assertEqual(record["normalized_invoice_number"], normalize_invoice_number(sample["data"]["invoice_number"]))
        self.assertEqual(record["context"]["source"]["type"], "manifest")

    def test_db_context_loader_uses_normalized_invoice_match_keys(self) -> None:
        sample = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4",
            seed=123,
            variation_index=0,
            today=date(2026, 7, 7),
        )
        invoice = _invoice_from_sample(sample)
        manifest = build_invoice_manifest([sample], pdf_filename="invoice.pdf")
        record = iter_ap_context_records_from_manifest(manifest, owner_user_id="user-123")[0]
        record = {
            **record,
            "id": 99,
            "_match_strategy": "vendor_po",
        }

        class Repo:
            def __init__(self) -> None:
                self.query: dict[str, Any] | None = None

            def find_ap_context_record(self, **kwargs: Any) -> dict[str, Any]:
                self.query = kwargs
                return record

        repo = Repo()
        context = load_db_procurement_context(repo, owner_user_id="user-123", invoice=invoice)

        self.assertEqual(repo.query["owner_user_id"], "user-123")
        self.assertEqual(repo.query["normalized_vendor"], invoice["vendor"]["normalized_name"])
        self.assertEqual(repo.query["normalized_purchase_order"], invoice["purchase_order"]["normalized"])
        self.assertEqual(repo.query["normalized_invoice_number"], invoice["invoice_number"]["normalized"])
        self.assertEqual(context["source"]["type"], "ap_context_records")
        self.assertEqual(context["source"]["record_id"], 99)
        self.assertEqual(context["source"]["match_strategy"], "vendor_po")

    def test_repository_ranks_strong_po_candidates_when_vendor_has_typo(self) -> None:
        rows = [
            {
                "id": 1,
                "normalized_vendor": "unrelated vendor",
                "context": {},
                "source_metadata": {},
            },
            {
                "id": 2,
                "normalized_vendor": "acme supplies",
                "context": {},
                "source_metadata": {},
            },
        ]

        class Cursor:
            def fetchall(self) -> list[dict[str, Any]]:
                return rows

        class Connection:
            def __enter__(self) -> "Connection":
                return self

            def __exit__(self, *args: Any) -> None:
                return None

            def execute(self, query: str, params: tuple[Any, ...]) -> Cursor:
                self.query = query
                self.params = params
                return Cursor()

        connection = Connection()

        class Database:
            def connect(self) -> Connection:
                return connection

        record = MailRepository(Database()).find_ap_context_record(  # type: ignore[arg-type]
            owner_user_id="user-123",
            normalized_vendor="acme supples",
            normalized_purchase_order="PO1000",
            normalized_invoice_number=None,
            amount_due=None,
            issue_date=None,
        )

        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(record["id"], 2)
        self.assertEqual(record["_match_strategy"], "fuzzy_vendor_po")
        self.assertEqual(connection.params, ("user-123", "PO1000"))

    def test_db_backed_ap_context_exercises_decision_scenarios(self) -> None:
        cases = {
            0: "approve",
            1: "approve_partial_consumption",
            2: "approve_with_tolerance",
            3: "needs_review",
            4: "flag_possible_duplicate",
            5: "needs_review",
            6: "block_or_escalate",
            7: "apply_credit_or_route_review",
        }

        for variation_index, expected_decision in cases.items():
            with self.subTest(variation_index=variation_index):
                sample = generate_invoice(
                    template_slug="ledger-clean",
                    paper_slug="a4",
                    seed=123,
                    variation_index=variation_index,
                    today=date(2026, 7, 7),
                )
                invoice = _invoice_from_sample(sample)
                manifest = build_invoice_manifest([sample], pdf_filename="invoice.pdf")
                record = iter_ap_context_records_from_manifest(manifest, owner_user_id="user-123")[0]
                record = {
                    **record,
                    "id": variation_index + 1,
                    "_match_strategy": "vendor_po",
                }

                class Repo:
                    def find_ap_context_record(self, **kwargs: Any) -> dict[str, Any]:
                        return record

                context = load_db_procurement_context(Repo(), owner_user_id="user-123", invoice=invoice)
                decision = decide_invoice(invoice, context)

                self.assertEqual(decision["decision"], expected_decision)


class InvoiceDecisionIntegrationTests(unittest.TestCase):
    def test_generated_pdf_manifest_parse_normalize_and_decide(self) -> None:
        sample = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4-half-horizontal",
            seed=123,
            variation_index=1,
            today=date(2026, 7, 7),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "partial.pdf"
            pdf_path.write_bytes(render_invoice_pdf([sample]))
            manifest_path = write_invoice_manifest(pdf_path, [sample])

            parsed = parse_invoice_pdf(pdf_path.read_bytes(), source_id=str(pdf_path))
            invoice = normalize_invoice_parse(parsed)
            context = load_procurement_context(manifest_path, invoice=invoice)
            decision = decide_invoice(invoice, context)

        self.assertEqual(parsed["status"], "parsed")
        self.assertEqual(decision["decision"], "approve_partial_consumption")
        critical = next(check for check in decision["checks"] if check["id"] == "critical_fields")
        partial = next(check for check in decision["checks"] if check["id"] == "partial_po_consumption")
        self.assertIn("amount_due", critical["evidence"])
        self.assertIsNotNone(critical["evidence"]["amount_due"]["bbox"])
        self.assertEqual(partial["context"]["purchase_order"]["po_number"], "PO-10000-PART")
        self.assertEqual(partial["context"]["remaining_after_invoice"], "3000.00")

    def test_demo_cli_runner_writes_audit_and_reviews_without_context(self) -> None:
        sample = generate_invoice(
            template_slug="ledger-clean",
            paper_slug="a4-half-horizontal",
            seed=321,
            variation_index=0,
            today=date(2026, 7, 7),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            pdf_path = Path(temp_dir) / "invoice.pdf"
            output_path = Path(temp_dir) / "invoice.audit.json"
            pdf_path.write_bytes(render_invoice_pdf([sample]))

            result = run_invoice_decision(
                pdf_path=pdf_path,
                output_path=output_path,
            )

            self.assertTrue(output_path.exists())
            self.assertEqual(result["decision"]["decision"], "needs_review")
            self.assertEqual(result["procurement_context"]["available"], False)


if __name__ == "__main__":
    unittest.main()
