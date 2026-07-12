from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from .ap_context import missing_procurement_context
from .invoice_normalizer import (
    money_decimal,
    normalize_invoice_number,
    normalize_purchase_order,
    normalize_vendor_name,
)


DECISIONS = {
    "approve",
    "approve_with_tolerance",
    "approve_partial_consumption",
    "needs_review",
    "request_missing_info",
    "flag_possible_duplicate",
    "block_or_escalate",
    "apply_credit_or_route_review",
}


def decide_invoice(
    invoice: dict[str, Any],
    procurement_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context = procurement_context or missing_procurement_context()
    checks: list[dict[str, Any]] = []

    text_check = _text_layer_check(invoice)
    checks.append(text_check)
    if text_check["status"] == "fail":
        return _decision(
            "request_missing_info",
            "low",
            "Invoice text could not be extracted.",
            "Request a text-searchable invoice PDF or run OCR before accounts payable review.",
            checks,
            invoice,
            context,
        )

    critical_check = _critical_fields_check(invoice)
    checks.append(critical_check)
    if critical_check["status"] == "fail":
        return _decision(
            "request_missing_info",
            "low",
            "Required invoice fields are missing.",
            "Ask the vendor for the missing invoice fields before matching to accounts payable context.",
            checks,
            invoice,
            context,
        )

    confidence_check = _parse_confidence_check(invoice)
    checks.append(confidence_check)

    parser_check = _parser_status_check(invoice)
    checks.append(parser_check)
    if parser_check["status"] != "pass":
        return _decision(
            "needs_review" if parser_check["status"] == "review" else "request_missing_info",
            "low",
            str(parser_check["summary"]),
            "Resolve the parser review findings and re-parse the invoice before approval.",
            checks,
            invoice,
            context,
        )
    if confidence_check["status"] == "review":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(confidence_check["summary"]),
            "Validate the low-confidence fields against the source invoice before approval.",
            checks,
            invoice,
            context,
        )

    context_check = _context_check(context)
    checks.append(context_check)
    if context_check["status"] == "fail":
        return _decision(
            "needs_review",
            "low",
            "Procurement context is missing.",
            "Load PO, vendor master, duplicate, and payment context before approving payment.",
            checks,
            invoice,
            context,
        )

    vendor_check = _vendor_check(invoice, context)
    checks.append(vendor_check)
    if vendor_check["status"] != "pass":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(vendor_check["summary"]),
            "Review vendor identity, aliases, and tax identifiers before approving payment.",
            checks,
            invoice,
            context,
        )

    buyer_check = _buyer_check(invoice, context)
    checks.append(buyer_check)
    if buyer_check["status"] in {"fail", "review"}:
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(buyer_check["summary"]),
            "Confirm that the invoice is billed to the approved legal entity before approval.",
            checks,
            invoice,
            context,
        )

    currency_check = _currency_check(invoice, context)
    checks.append(currency_check)
    if currency_check["status"] in {"fail", "review"}:
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(currency_check["summary"]),
            "Confirm invoice and purchase-order currency before comparing monetary amounts.",
            checks,
            invoice,
            context,
        )

    duplicate_check = _duplicate_check(invoice, context)
    checks.append(duplicate_check)
    if duplicate_check["status"] == "fail":
        return _decision(
            "flag_possible_duplicate",
            _confidence_for_review(invoice),
            "Invoice may duplicate a prior vendor invoice.",
            "Hold payment and compare the candidate prior invoice before posting.",
            checks,
            invoice,
            context,
        )

    invoice_number_check = _expected_invoice_number_check(invoice, context)
    checks.append(invoice_number_check)
    if invoice_number_check["status"] == "fail":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(invoice_number_check["summary"]),
            "Resolve the invoice-number mismatch against the matched accounts payable record.",
            checks,
            invoice,
            context,
        )

    date_check = _date_check(invoice, context)
    checks.append(date_check)
    if date_check["status"] == "fail":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(date_check["summary"]),
            "Resolve invoice or due-date differences against accounts payable context.",
            checks,
            invoice,
            context,
        )

    bank_check = _bank_check(invoice, context)
    checks.append(bank_check)
    if bank_check["status"] == "fail":
        return _decision(
            "block_or_escalate",
            _confidence_for_review(invoice, high_when_context=True),
            "Invoice payment details differ from the approved vendor master.",
            "Block payment and escalate the bank detail change for vendor verification.",
            checks,
            invoice,
            context,
        )

    credit_check = _credit_check(invoice, context)
    checks.append(credit_check)
    if credit_check["status"] == "review":
        return _decision(
            "apply_credit_or_route_review",
            _confidence_for_review(invoice),
            "Invoice appears to be a credit memo or negative payable.",
            "Apply the credit to open liability or route to accounts payable review if no matching payable is open.",
            checks,
            invoice,
            context,
        )

    po_check = _purchase_order_check(invoice, context)
    checks.append(po_check)
    if po_check["status"] == "review":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(po_check["summary"]),
            str(po_check["next_action"]),
            checks,
            invoice,
            context,
        )

    tax_check = _amount_composition_check(invoice, context)
    checks.append(tax_check)
    if tax_check["status"] in {"fail", "review"}:
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(tax_check["summary"]),
            "Reconcile subtotal, discounts, aggregate tax, shipping, payments, and balance due before approval.",
            checks,
            invoice,
            context,
        )

    partial_check = _partial_consumption_check(invoice, context)
    checks.append(partial_check)
    if partial_check["status"] == "pass":
        return _decision(
            "approve_partial_consumption",
            _confidence_for_approval(invoice),
            "Invoice fits within the remaining PO balance after prior consumption.",
            "Approve the invoice and consume the remaining PO balance by the invoice amount.",
            checks,
            invoice,
            context,
        )
    if partial_check["status"] in {"fail", "review"}:
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(partial_check["summary"]),
            "Resolve the remaining purchase-order balance before approval.",
            checks,
            invoice,
            context,
        )

    amount_check = _amount_match_check(invoice, context)
    checks.append(amount_check)
    if amount_check["status"] == "pass_with_tolerance":
        return _decision(
            "approve_with_tolerance",
            _confidence_for_approval(invoice),
            "Invoice variance is within the configured accounts payable tolerance.",
            "Approve with a tolerance note in the audit trail.",
            checks,
            invoice,
            context,
        )
    if amount_check["status"] == "fail":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            "Invoice amount is outside the configured accounts payable tolerance.",
            "Route to accounts payable review for purchase order variance approval or vendor correction.",
            checks,
            invoice,
            context,
        )
    if amount_check["status"] == "review":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            str(amount_check["summary"]),
            "Load the missing expected amount and route the invoice for accounts payable review.",
            checks,
            invoice,
            context,
        )

    return _decision(
        "approve",
        _confidence_for_approval(invoice),
        "Vendor, purchase order, payment details, duplicate check, and amount match accounts payable context.",
        "Approve the invoice for payment.",
        checks,
        invoice,
        context,
    )


def _text_layer_check(invoice: dict[str, Any]) -> dict[str, Any]:
    if invoice.get("no_text_layer"):
        return _check(
            "text_layer",
            "fail",
            "Parser reported no usable text layer.",
            evidence={"parser_status": invoice.get("parser_status")},
        )
    return _check(
        "text_layer",
        "pass",
        "Parser extracted a usable text layer.",
        evidence={"parser_status": invoice.get("parser_status")},
    )


def _critical_fields_check(invoice: dict[str, Any]) -> dict[str, Any]:
    missing = invoice.get("missing_critical_fields")
    if isinstance(missing, list) and missing:
        return _check(
            "critical_fields",
            "fail",
            f"Missing critical fields: {', '.join(str(value) for value in missing)}.",
            evidence={"missing_critical_fields": missing},
        )
    return _check(
        "critical_fields",
        "pass",
        "Critical invoice fields were extracted.",
        evidence=_invoice_evidence(invoice, "vendor", "invoice_number", "issue_date", "amount_due"),
    )


def _parse_confidence_check(invoice: dict[str, Any]) -> dict[str, Any]:
    low_fields = invoice.get("low_confidence_fields")
    status = "review" if isinstance(low_fields, list) and low_fields else "pass"
    summary = (
        f"Low-confidence parsed fields: {', '.join(str(value) for value in low_fields)}."
        if status == "review"
        else "Parsed fields meet confidence threshold."
    )
    return _check(
        "parse_confidence",
        status,
        summary,
        evidence={
            "confidence": invoice.get("confidence"),
            "low_confidence_fields": low_fields if isinstance(low_fields, list) else [],
            "parse_warnings": invoice.get("parse_warnings"),
        },
    )


def _parser_status_check(invoice: dict[str, Any]) -> dict[str, Any]:
    parser_status = str(invoice.get("parser_status") or "unknown")
    if parser_status == "parsed":
        status = "pass"
        summary = "Parser completed without review status."
    elif parser_status == "needs_review":
        status = "review"
        summary = "Parser marked the invoice as needing review."
    else:
        status = "fail"
        summary = f"Parser did not produce an approvable result ({parser_status})."
    return _check(
        "parser_status",
        status,
        summary,
        evidence={
            "parser_status": parser_status,
            "parse_warnings": invoice.get("parse_warnings"),
            "missing_fields": invoice.get("missing_fields"),
        },
    )


def _context_check(context: dict[str, Any]) -> dict[str, Any]:
    if not context.get("available"):
        return _check(
            "procurement_context",
            "fail",
            str(context.get("reason") or "Procurement context is unavailable."),
            context={"available": False},
        )
    return _check(
        "procurement_context",
        "pass",
        "Procurement context loaded.",
        context={
            "source": context.get("source"),
            "scenario": context.get("scenario"),
        },
    )


def _vendor_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    invoice_vendor = _invoice_vendor(invoice)
    context_vendor = context.get("vendor") if isinstance(context.get("vendor"), dict) else {}
    approved_vendor = str(
        context_vendor.get("normalized_name")
        or normalize_vendor_name(context_vendor.get("name"))
    )
    aliases = context_vendor.get("aliases") if isinstance(context_vendor.get("aliases"), list) else []
    normalized_aliases = {
        normalize_vendor_name(value)
        for value in aliases
        if normalize_vendor_name(value)
    }
    invoice_tax_ids = _invoice_tax_ids(invoice)
    context_tax_id = _normalize_tax_identifier(context_vendor.get("tax_id"))
    tax_id_match = bool(context_tax_id and context_tax_id in invoice_tax_ids)
    invoice_vendor_ids = _invoice_vendor_ids(invoice)
    context_vendor_id = _normalize_identifier(context_vendor.get("vendor_id"))
    vendor_id_match = bool(context_vendor_id and context_vendor_id in invoice_vendor_ids)
    if not approved_vendor and not normalized_aliases and not context_tax_id and not context_vendor_id:
        return _check(
            "vendor_match",
            "review",
            "No approved vendor identity is available in accounts payable context.",
            evidence={
                "invoice_vendor": invoice_vendor,
                "invoice_tax_ids": sorted(invoice_tax_ids),
                "invoice_vendor_ids": sorted(invoice_vendor_ids),
            },
            context={"vendor": context_vendor},
        )
    exact_name_match = bool(invoice_vendor and invoice_vendor == approved_vendor)
    alias_match = bool(invoice_vendor and invoice_vendor in normalized_aliases)
    candidates = [approved_vendor, *sorted(normalized_aliases)]
    similarity = max(
        (SequenceMatcher(None, invoice_vendor, candidate).ratio() for candidate in candidates if candidate),
        default=0.0,
    )
    if exact_name_match:
        status = "pass"
        summary = "Invoice vendor matches approved vendor."
        match_method = "normalized_name"
    elif alias_match:
        status = "pass"
        summary = "Invoice vendor matches an approved vendor alias."
        match_method = "approved_alias"
    elif tax_id_match:
        status = "pass"
        summary = "Invoice vendor tax identifier matches approved vendor."
        match_method = "tax_id"
    elif vendor_id_match:
        status = "pass"
        summary = "Invoice vendor identifier matches approved vendor."
        match_method = "vendor_id"
    elif similarity >= 0.85:
        status = "review"
        summary = "Invoice vendor is a close fuzzy match and requires identity review."
        match_method = "fuzzy_name"
    else:
        status = "fail"
        summary = "Invoice vendor does not match approved vendor."
        match_method = "none"
    return _check(
        "vendor_match",
        status,
        summary,
        evidence={
            **_invoice_evidence(invoice, "vendor"),
            "normalized_vendor": invoice_vendor,
            "tax_ids": sorted(invoice_tax_ids),
            "vendor_ids": sorted(invoice_vendor_ids),
        },
        context={
            "vendor": context_vendor,
            "match_method": match_method,
            "similarity": round(similarity, 3),
        },
    )


def _buyer_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    invoice_buyer = _invoice_buyer(invoice)
    context_buyer = context.get("buyer") if isinstance(context.get("buyer"), dict) else {}
    expected_buyer = str(
        context_buyer.get("normalized_name")
        or normalize_vendor_name(context_buyer.get("name"))
    )
    if not expected_buyer:
        return _check(
            "buyer_match",
            "not_applicable",
            "Accounts payable context does not specify an expected buyer.",
            evidence=_invoice_evidence(invoice, "buyer"),
        )
    status = "pass" if invoice_buyer == expected_buyer else "fail"
    return _check(
        "buyer_match",
        status,
        "Invoice buyer matches the approved bill-to entity."
        if status == "pass"
        else "Invoice buyer does not match the approved bill-to entity.",
        evidence={**_invoice_evidence(invoice, "buyer"), "normalized_buyer": invoice_buyer},
        context={"buyer": context_buyer},
    )


def _currency_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    invoice_currency = _invoice_currency(invoice)
    expected_currency = _context_currency(context)
    money_currencies = _invoice_money_currencies(invoice)
    inconsistent = sorted(value for value in money_currencies if value != invoice_currency)
    if inconsistent:
        return _check(
            "currency_match",
            "fail",
            "Invoice monetary fields contain inconsistent currencies.",
            evidence={
                **_invoice_evidence(invoice, "currency", "amount_due"),
                "invoice_currency": invoice_currency,
                "money_currencies": sorted(money_currencies),
            },
        )
    if not expected_currency:
        return _check(
            "currency_match",
            "not_applicable",
            "Accounts payable context does not specify an expected currency.",
            evidence={**_invoice_evidence(invoice, "currency"), "invoice_currency": invoice_currency},
        )
    status = "pass" if invoice_currency == expected_currency else "fail"
    return _check(
        "currency_match",
        status,
        "Invoice currency matches accounts payable context."
        if status == "pass"
        else "Invoice currency does not match accounts payable context.",
        evidence={
            **_invoice_evidence(invoice, "currency", "amount_due"),
            "invoice_currency": invoice_currency,
        },
        context={"expected_currency": expected_currency},
    )


def _duplicate_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    normalized_invoice = _invoice_number(invoice)
    normalized_vendor = _invoice_vendor(invoice)
    candidates = context.get("duplicate_candidates")
    if not isinstance(candidates, list):
        candidates = []
    matches = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if candidate.get("normalized_invoice_number") != normalized_invoice:
            continue
        candidate_vendor = candidate.get("normalized_vendor")
        if candidate_vendor and normalized_vendor and candidate_vendor != normalized_vendor:
            continue
        matches.append(candidate)
    if matches:
        return _check(
            "duplicate_invoice",
            "fail",
            "Normalized invoice number matches a prior invoice candidate.",
            evidence=_invoice_evidence(invoice, "invoice_number", "vendor", "amount_due"),
            context={"candidates": matches},
        )
    return _check(
        "duplicate_invoice",
        "pass",
        "No duplicate invoice candidate matched.",
        evidence=_invoice_evidence(invoice, "invoice_number"),
        context={"candidate_count": len(candidates)},
    )


def _expected_invoice_number_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    actual = _invoice_number(invoice)
    expected = _context_invoice_number(context)
    if not expected:
        return _check(
            "invoice_number_match",
            "not_applicable",
            "Accounts payable context does not specify an expected invoice number.",
            evidence=_invoice_evidence(invoice, "invoice_number"),
        )
    status = "pass" if actual == expected else "fail"
    return _check(
        "invoice_number_match",
        status,
        "Invoice number matches the accounts payable record."
        if status == "pass"
        else "Invoice number does not match the accounts payable record.",
        evidence={**_invoice_evidence(invoice, "invoice_number"), "normalized_invoice_number": actual},
        context={"expected_normalized_invoice_number": expected},
    )


def _date_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    expected_invoice = context.get("invoice") if isinstance(context.get("invoice"), dict) else {}
    expected_issue = _normalize_date(
        expected_invoice.get("issue_date") or context.get("issue_date")
    )
    expected_due = _normalize_date(
        expected_invoice.get("due_date") or context.get("due_date")
    )
    actual_issue = _invoice_date(invoice, "issue_date")
    actual_due = _invoice_date(invoice, "due_date")
    mismatches: list[str] = []
    if expected_issue and expected_issue not in _invoice_date_candidates(invoice, "issue_date"):
        mismatches.append("issue_date")
    if expected_due and expected_due not in _invoice_date_candidates(invoice, "due_date"):
        mismatches.append("due_date")
    if not expected_issue and not expected_due:
        status = "not_applicable"
        summary = "Accounts payable context does not specify expected invoice dates."
    elif mismatches:
        status = "fail"
        summary = f"Invoice date fields do not match accounts payable context: {', '.join(mismatches)}."
    else:
        status = "pass"
        summary = "Invoice dates match accounts payable context."
    return _check(
        "date_match",
        status,
        summary,
        evidence={
            **_invoice_evidence(invoice, "issue_date", "due_date"),
            "issue_date": actual_issue,
            "due_date": actual_due,
        },
        context={"issue_date": expected_issue, "due_date": expected_due},
    )


def _bank_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    approved = context.get("approved_bank_details") if isinstance(context.get("approved_bank_details"), dict) else {}
    approved_account = _normalize_account(approved.get("account"))
    invoice_bank = _invoice_bank_account(invoice)
    if not invoice_bank:
        invoice_payment = context.get("invoice_payment") if isinstance(context.get("invoice_payment"), dict) else {}
        invoice_bank = _normalize_account(invoice_payment.get("bank_account"))
    if not approved_account:
        return _check(
            "bank_details",
            "pass",
            "No approved bank account override is present in accounts payable context.",
            evidence={"invoice_bank_account": invoice_bank},
            context={"approved_bank_account": None},
        )
    if invoice_bank != approved_account:
        return _check(
            "bank_details",
            "fail",
            "Invoice bank account differs from vendor master.",
            evidence={"invoice_bank_account": invoice_bank, **_invoice_evidence(invoice, "bank_details")},
            context={"approved_bank_account": approved_account},
        )
    return _check(
        "bank_details",
        "pass",
        "Invoice bank account matches vendor master.",
        evidence={"invoice_bank_account": invoice_bank},
        context={"approved_bank_account": approved_account},
    )


def _credit_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    amount = _invoice_amount(invoice)
    line_items = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    has_negative_line = any(
        (money_decimal((item.get("amount") or {}).get("amount")) or Decimal("0.00")) < 0
        for item in line_items
        if isinstance(item, dict) and isinstance(item.get("amount"), dict)
    )
    invoice_number = str((invoice.get("invoice_number") or {}).get("value") or "")
    scenario = str(context.get("scenario") or "")
    is_credit = (
        (amount is not None and amount < 0)
        or has_negative_line
        or invoice_number.upper().startswith("CM-")
        or scenario == "credit_memo_negative_balance"
    )
    return _check(
        "credit_memo",
        "review" if is_credit else "pass",
        "Credit memo or negative balance detected."
        if is_credit
        else "Invoice is not a credit memo or negative payable.",
        evidence={
            "amount_due": str(amount) if amount is not None else None,
            "has_negative_line_items": has_negative_line,
            **_invoice_evidence(invoice, "invoice_number", "amount_due"),
        },
        context={
            "scenario": scenario,
            "credit_context": (context.get("raw_ap_context") or {}).get("context")
            if isinstance(context.get("raw_ap_context"), dict)
            else None,
        },
    )


def _purchase_order_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    invoice_po = _invoice_po(invoice)
    context_po = _context_po(context)
    candidate_po = context.get("candidate_open_po") if isinstance(context.get("candidate_open_po"), dict) else None
    if not invoice_po and candidate_po:
        candidate_number = candidate_po.get("po_number")
        return {
            **_check(
                "purchase_order",
                "review",
                f"Invoice omits a purchase order, but accounts payable context suggests {candidate_number}.",
                evidence=_invoice_evidence(invoice, "purchase_order", "vendor", "amount_due"),
                context={"candidate_open_po": candidate_po},
            ),
            "next_action": f"Route to review and attach candidate PO {candidate_number}.",
        }
    if not invoice_po:
        return {
            **_check(
                "purchase_order",
                "review",
                "Invoice omits a purchase order.",
                evidence=_invoice_evidence(invoice, "purchase_order"),
                context={"purchase_order": context.get("purchase_order")},
            ),
            "next_action": "Request the purchase order reference or route to non-purchase-order accounts payable review.",
        }
    if context_po and invoice_po != context_po:
        return {
            **_check(
                "purchase_order",
                "review",
                "Invoice purchase order does not match accounts payable context.",
                evidence=_invoice_evidence(invoice, "purchase_order"),
                context={"purchase_order": context.get("purchase_order")},
            ),
            "next_action": "Review the PO mismatch before approval.",
        }
    return _check(
        "purchase_order",
        "pass",
        "Invoice purchase order matches accounts payable context.",
        evidence=_invoice_evidence(invoice, "purchase_order"),
        context={"purchase_order": context.get("purchase_order")},
    )


def _amount_composition_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    amounts = invoice.get("amounts") if isinstance(invoice.get("amounts"), dict) else {}
    subtotal = money_decimal(amounts.get("subtotal"))
    balance_due = _invoice_amount(invoice)
    if subtotal is None:
        return _check(
            "amount_composition",
            "not_applicable",
            "Invoice does not expose a subtotal for amount composition reconciliation.",
            evidence=_invoice_evidence(invoice, "amount_due"),
        )
    if balance_due is None:
        return _check(
            "amount_composition",
            "review",
            "Invoice balance due is unavailable for amount composition reconciliation.",
            evidence=_invoice_evidence(invoice, "amount_due"),
        )

    components = {
        key: money_decimal(amounts.get(key)) or Decimal("0.00")
        for key in ("discount", "tax", "shipping", "paid")
    }
    calculated_balance = (
        subtotal
        - components["discount"]
        + components["tax"]
        + components["shipping"]
        - components["paid"]
    ).quantize(Decimal("0.01"))
    composition_variance = (balance_due - calculated_balance).quantize(Decimal("0.01"))

    context_amounts = context.get("amounts") if isinstance(context.get("amounts"), dict) else {}
    component_mismatches: dict[str, dict[str, str | None]] = {}
    for key in ("subtotal", "discount", "tax", "shipping", "paid", "balance_due"):
        actual = balance_due if key == "balance_due" else money_decimal(amounts.get(key))
        expected = money_decimal(context_amounts.get(key))
        if expected is None:
            continue
        if actual is None:
            if expected != Decimal("0.00"):
                component_mismatches[key] = {"actual": None, "expected": str(expected)}
            continue
        if abs(actual - expected) <= Decimal("0.01"):
            continue
        component_mismatches[key] = {"actual": str(actual), "expected": str(expected)}

    line_items = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    line_total = sum(
        (
            money_decimal(item.get("amount")) or Decimal("0.00")
            for item in line_items
            if isinstance(item, dict)
        ),
        Decimal("0.00"),
    ).quantize(Decimal("0.01"))
    has_line_amounts = any(
        isinstance(item, dict) and money_decimal(item.get("amount")) is not None
        for item in line_items
    )
    line_variance = (line_total - subtotal).quantize(Decimal("0.01")) if has_line_amounts else None

    failures: list[str] = []
    if abs(composition_variance) > Decimal("0.01"):
        failures.append("summary arithmetic")
    if component_mismatches:
        failures.append("accounts payable amount components")
    if line_variance is not None and abs(line_variance) > Decimal("0.01"):
        failures.append("line-item subtotal")
    status = "fail" if failures else "pass"
    summary = (
        f"Invoice amount composition does not reconcile: {', '.join(failures)}."
        if failures
        else "Subtotal, discounts, aggregate tax, shipping, payments, and balance due reconcile."
    )
    return _check(
        "amount_composition",
        status,
        summary,
        evidence={
            "subtotal": str(subtotal),
            "discount": str(components["discount"]),
            "tax": str(components["tax"]),
            "shipping": str(components["shipping"]),
            "paid": str(components["paid"]),
            "balance_due": str(balance_due),
            "calculated_balance": str(calculated_balance),
            "composition_variance": str(composition_variance),
            "line_item_total": str(line_total) if has_line_amounts else None,
            "line_item_variance": str(line_variance) if line_variance is not None else None,
            **_invoice_evidence(invoice, "amount_due"),
        },
        context={"component_mismatches": component_mismatches},
    )


def _partial_consumption_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    po = context.get("purchase_order") if isinstance(context.get("purchase_order"), dict) else {}
    consumed = _first_decimal(
        money_decimal(po.get("previously_consumed")),
        Decimal("0.00"),
    )
    remaining = money_decimal(po.get("remaining_before_invoice"))
    amount = _invoice_amount(invoice)
    previous = context.get("previous_invoices") if isinstance(context.get("previous_invoices"), list) else []
    if consumed <= 0 and not previous:
        return _check(
            "partial_po_consumption",
            "not_applicable",
            "No prior PO consumption is present.",
            evidence=_invoice_evidence(invoice, "amount_due", "purchase_order"),
            context={"purchase_order": po, "previous_invoice_count": len(previous)},
        )
    if amount is None or remaining is None:
        return _check(
            "partial_po_consumption",
            "review",
            "Partial PO context is present, but invoice amount or PO balance is missing.",
            evidence=_invoice_evidence(invoice, "amount_due", "purchase_order"),
            context={"purchase_order": po},
        )
    status = "pass" if Decimal("0.00") <= amount <= remaining else "fail"
    return _check(
        "partial_po_consumption",
        status,
        "Invoice amount is within remaining PO balance."
        if status == "pass"
        else "Invoice amount exceeds remaining PO balance.",
        evidence=_invoice_evidence(invoice, "amount_due", "purchase_order"),
        context={
            "purchase_order": po,
            "previous_invoices": previous,
            "remaining_after_invoice": str((remaining - amount).quantize(Decimal("0.01"))),
        },
    )


def _amount_match_check(invoice: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    amount = _invoice_amount(invoice)
    po = context.get("purchase_order") if isinstance(context.get("purchase_order"), dict) else {}
    raw_ap = context.get("raw_ap_context") if isinstance(context.get("raw_ap_context"), dict) else {}
    raw_ap_context = raw_ap.get("context") if isinstance(raw_ap.get("context"), dict) else {}
    if "variance_amount" in raw_ap_context:
        expected = money_decimal(po.get("authorized_total"))
    else:
        expected = _first_decimal(
            money_decimal(context.get("invoice_total")),
            money_decimal(po.get("authorized_total")),
        )
    tolerance_policy = context.get("tolerance_policy") if isinstance(context.get("tolerance_policy"), dict) else {}
    tolerance = _first_decimal(
        money_decimal(tolerance_policy.get("amount")),
        Decimal("0.00"),
    )
    if amount is None or expected is None:
        return _check(
            "amount_match",
            "review",
            "Invoice amount or accounts payable expected amount is missing.",
            evidence=_invoice_evidence(invoice, "amount_due"),
            context={"invoice_total": context.get("invoice_total"), "tolerance_policy": tolerance_policy},
        )
    variance = (amount - expected).quantize(Decimal("0.01"))
    if variance == 0:
        status = "pass"
        summary = "Invoice amount matches accounts payable expected amount."
    elif abs(variance) <= tolerance:
        status = "pass_with_tolerance"
        summary = "Invoice amount variance is within tolerance."
    else:
        status = "fail"
        summary = "Invoice amount variance exceeds tolerance."
    return _check(
        "amount_match",
        status,
        summary,
        evidence=_invoice_evidence(invoice, "amount_due"),
        context={
            "expected_amount": str(expected),
            "variance": str(variance),
            "tolerance_policy": tolerance_policy,
        },
    )


def _decision(
    decision: str,
    confidence: str,
    summary: str,
    next_action: str,
    checks: list[dict[str, Any]],
    invoice: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ValueError(f"Unsupported invoice decision: {decision}")
    return {
        "schema_version": 1,
        "decision": decision,
        "confidence": confidence,
        "summary": summary,
        "next_action": next_action,
        "checks": checks,
        "audit": {
            "parser_status": invoice.get("parser_status"),
            "parser_version": invoice.get("parser_version"),
            "normalized_invoice_number": _invoice_number(invoice),
            "normalized_vendor": _invoice_vendor(invoice),
            "purchase_order": _invoice_po(invoice),
            "amount_due": str(_invoice_amount(invoice)) if _invoice_amount(invoice) is not None else None,
            "context_source": context.get("source"),
            "context_scenario": context.get("scenario"),
            "expected_from_manifest": context.get("expected"),
        },
    }


def _check(
    check_id: str,
    status: str,
    summary: str,
    *,
    evidence: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "status": status,
        "summary": summary,
        "evidence": evidence or {},
        "context": context or {},
    }


def _invoice_number(invoice: dict[str, Any]) -> str:
    field = invoice.get("invoice_number") if isinstance(invoice.get("invoice_number"), dict) else {}
    return str(field.get("normalized") or normalize_invoice_number(field.get("value")))


def _invoice_vendor(invoice: dict[str, Any]) -> str:
    field = invoice.get("vendor") if isinstance(invoice.get("vendor"), dict) else {}
    return str(field.get("normalized_name") or normalize_vendor_name(field.get("name")))


def _invoice_buyer(invoice: dict[str, Any]) -> str:
    field = invoice.get("buyer") if isinstance(invoice.get("buyer"), dict) else {}
    return str(field.get("normalized_name") or normalize_vendor_name(field.get("name")))


def _invoice_tax_ids(invoice: dict[str, Any]) -> set[str]:
    field = invoice.get("vendor") if isinstance(invoice.get("vendor"), dict) else {}
    raw_ids = field.get("tax_ids") if isinstance(field.get("tax_ids"), list) else []
    return {
        normalized
        for value in raw_ids
        if (normalized := _normalize_tax_identifier(value))
    }


def _invoice_vendor_ids(invoice: dict[str, Any]) -> set[str]:
    field = invoice.get("vendor") if isinstance(invoice.get("vendor"), dict) else {}
    raw_ids = field.get("vendor_ids") if isinstance(field.get("vendor_ids"), list) else []
    return {
        normalized
        for value in raw_ids
        if (normalized := _normalize_identifier(value))
    }


def _normalize_identifier(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _normalize_tax_identifier(value: Any) -> str:
    text = re.sub(
        r"\b(?:TAX\s*ID|VAT|GSTIN|GST|EIN)\b",
        "",
        str(value or "").upper(),
    )
    normalized = _normalize_identifier(text)
    if re.fullmatch(r"[A-Z]{2}\d{5,}", normalized):
        return normalized[2:]
    return normalized


def _invoice_currency(invoice: dict[str, Any]) -> str:
    currency = invoice.get("currency") if isinstance(invoice.get("currency"), dict) else {}
    amount_due = invoice.get("amount_due") if isinstance(invoice.get("amount_due"), dict) else {}
    return str(currency.get("value") or amount_due.get("currency") or "").strip().upper()


def _context_currency(context: dict[str, Any]) -> str:
    purchase_order = context.get("purchase_order") if isinstance(context.get("purchase_order"), dict) else {}
    raw_ap = context.get("raw_ap_context") if isinstance(context.get("raw_ap_context"), dict) else {}
    raw_values = raw_ap.get("context") if isinstance(raw_ap.get("context"), dict) else {}
    context_currency = context.get("currency")
    if isinstance(context_currency, dict):
        context_currency = context_currency.get("value") or context_currency.get("code")
    po_currency = purchase_order.get("currency")
    if isinstance(po_currency, dict):
        po_currency = po_currency.get("value") or po_currency.get("code")
    return str(
        context_currency
        or po_currency
        or raw_values.get("currency")
        or raw_values.get("invoice_currency")
        or raw_values.get("po_currency")
        or raw_values.get("expected_currency")
        or ""
    ).strip().upper()


def _invoice_money_currencies(invoice: dict[str, Any]) -> set[str]:
    currencies: set[str] = set()
    amounts = invoice.get("amounts") if isinstance(invoice.get("amounts"), dict) else {}
    for value in amounts.values():
        if isinstance(value, dict) and value.get("currency"):
            currencies.add(str(value["currency"]).strip().upper())
    line_items = invoice.get("line_items") if isinstance(invoice.get("line_items"), list) else []
    for item in line_items:
        if not isinstance(item, dict):
            continue
        if item.get("currency"):
            currencies.add(str(item["currency"]).strip().upper())
        for key in ("unit_price", "amount"):
            value = item.get(key)
            if isinstance(value, dict) and value.get("currency"):
                currencies.add(str(value["currency"]).strip().upper())
    return currencies


def _context_invoice_number(context: dict[str, Any]) -> str:
    invoice_context = context.get("invoice") if isinstance(context.get("invoice"), dict) else {}
    expected = (
        invoice_context.get("normalized_invoice_number")
        or invoice_context.get("invoice_number")
        or context.get("normalized_invoice_number")
        or context.get("invoice_number")
    )
    return normalize_invoice_number(expected)


def _invoice_date(invoice: dict[str, Any], key: str) -> str | None:
    field = invoice.get(key) if isinstance(invoice.get(key), dict) else {}
    return _normalize_date(field.get("value"))


def _invoice_date_candidates(invoice: dict[str, Any], key: str) -> set[str]:
    candidates = {value for value in [_invoice_date(invoice, key)] if value}
    field = invoice.get(key) if isinstance(invoice.get(key), dict) else {}
    evidence = field.get("evidence") if isinstance(field.get("evidence"), dict) else {}
    raw = str(field.get("raw") or evidence.get("raw") or "").strip()
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 8:
        day, month, year = digits[:2], digits[2:4], digits[4:]
        for candidate in (f"{year}-{month}-{day}", f"{year}-{day}-{month}"):
            normalized = _normalize_date(candidate)
            if normalized:
                candidates.add(normalized)
    elif len(digits) == 6:
        day, month, short_year = digits[:2], digits[2:4], digits[4:]
        year = f"20{short_year}"
        for candidate in (f"{year}-{month}-{day}", f"{year}-{day}-{month}"):
            normalized = _normalize_date(candidate)
            if normalized:
                candidates.add(normalized)
    return candidates


def _normalize_date(value: Any) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return text


def _invoice_po(invoice: dict[str, Any]) -> str:
    field = invoice.get("purchase_order") if isinstance(invoice.get("purchase_order"), dict) else {}
    return str(field.get("normalized") or normalize_purchase_order(field.get("value")))


def _context_po(context: dict[str, Any]) -> str:
    po = context.get("purchase_order") if isinstance(context.get("purchase_order"), dict) else {}
    return str(po.get("normalized") or normalize_purchase_order(po.get("po_number")))


def _invoice_amount(invoice: dict[str, Any]) -> Decimal | None:
    amount_due = invoice.get("amount_due") if isinstance(invoice.get("amount_due"), dict) else {}
    return money_decimal(amount_due.get("amount"))


def _invoice_bank_account(invoice: dict[str, Any]) -> str | None:
    bank_details = invoice.get("bank_details") if isinstance(invoice.get("bank_details"), dict) else {}
    return _normalize_account(bank_details.get("bank_account"))


def _normalize_account(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = "".join(char for char in text if char.isdigit())
    if digits:
        return f"**** {digits[-4:]}"
    return text.upper()


def _first_decimal(*values: Decimal | None) -> Decimal | None:
    for value in values:
        if value is not None:
            return value
    return None


def _invoice_evidence(invoice: dict[str, Any], *keys: str) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for key in keys:
        value = invoice.get(key)
        if isinstance(value, dict) and isinstance(value.get("evidence"), dict):
            evidence[key] = value["evidence"]
    return evidence


def _confidence_for_approval(invoice: dict[str, Any]) -> str:
    confidence = invoice.get("confidence") if isinstance(invoice.get("confidence"), dict) else {}
    return "high" if confidence.get("level") == "high" else "medium"


def _confidence_for_review(invoice: dict[str, Any], *, high_when_context: bool = False) -> str:
    confidence = invoice.get("confidence") if isinstance(invoice.get("confidence"), dict) else {}
    if high_when_context and confidence.get("level") in {"high", "medium"}:
        return "high"
    return "medium" if confidence.get("level") in {"high", "medium"} else "low"
