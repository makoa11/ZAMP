from __future__ import annotations

from decimal import Decimal
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
            "Request a text-searchable invoice PDF or run OCR before AP review.",
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
            "Ask the vendor for the missing invoice fields before matching to AP context.",
            checks,
            invoice,
            context,
        )

    confidence_check = _parse_confidence_check(invoice)
    checks.append(confidence_check)

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
            "Apply the credit to open liability or route to AP review if no matching payable is open.",
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

    amount_check = _amount_match_check(invoice, context)
    checks.append(amount_check)
    if amount_check["status"] == "pass_with_tolerance":
        return _decision(
            "approve_with_tolerance",
            _confidence_for_approval(invoice),
            "Invoice variance is within the configured AP tolerance.",
            "Approve with a tolerance note in the audit trail.",
            checks,
            invoice,
            context,
        )
    if amount_check["status"] == "fail":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            "Invoice amount is outside the configured AP tolerance.",
            "Route to AP review for PO variance approval or vendor correction.",
            checks,
            invoice,
            context,
        )

    if vendor_check["status"] == "fail":
        return _decision(
            "needs_review",
            _confidence_for_review(invoice),
            "Invoice vendor does not match the approved vendor context.",
            "Review vendor master and PO ownership before approving payment.",
            checks,
            invoice,
            context,
        )

    return _decision(
        "approve",
        _confidence_for_approval(invoice),
        "Vendor, PO, payment details, duplicate check, and amount match AP context.",
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
            "parse_warnings": invoice.get("parse_warnings"),
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
    if not context_vendor.get("normalized_name"):
        return _check(
            "vendor_match",
            "review",
            "No approved vendor name is available in AP context.",
            evidence={"invoice_vendor": invoice_vendor},
            context={"vendor": context_vendor},
        )
    status = "pass" if invoice_vendor == context_vendor.get("normalized_name") else "fail"
    return _check(
        "vendor_match",
        status,
        "Invoice vendor matches approved vendor."
        if status == "pass"
        else "Invoice vendor does not match approved vendor.",
        evidence=_invoice_evidence(invoice, "vendor"),
        context={"vendor": context_vendor},
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
            "No approved bank account override is present in AP context.",
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
                f"Invoice omits a PO, but AP context suggests {candidate_number}.",
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
            "next_action": "Request the PO reference or route to non-PO AP review.",
        }
    if context_po and invoice_po != context_po:
        return {
            **_check(
                "purchase_order",
                "review",
                "Invoice PO does not match AP context.",
                evidence=_invoice_evidence(invoice, "purchase_order"),
                context={"purchase_order": context.get("purchase_order")},
            ),
            "next_action": "Review the PO mismatch before approval.",
        }
    return _check(
        "purchase_order",
        "pass",
        "Invoice PO matches AP context.",
        evidence=_invoice_evidence(invoice, "purchase_order"),
        context={"purchase_order": context.get("purchase_order")},
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
            "Invoice amount or AP expected amount is missing.",
            evidence=_invoice_evidence(invoice, "amount_due"),
            context={"invoice_total": context.get("invoice_total"), "tolerance_policy": tolerance_policy},
        )
    variance = (amount - expected).quantize(Decimal("0.01"))
    if variance == 0:
        status = "pass"
        summary = "Invoice amount matches AP expected amount."
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
