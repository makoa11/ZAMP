from __future__ import annotations

import copy
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .invoice_normalizer import (
    money_decimal,
    normalize_invoice_number,
    normalize_money,
    normalize_purchase_order,
    normalize_vendor_name,
)


DEFAULT_TOLERANCE_PERCENT = Decimal("2.00")


def missing_procurement_context(reason: str = "Procurement context was not provided.") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "available": False,
        "reason": reason,
        "source": None,
        "checks": [],
    }


def load_db_procurement_context(
    repo: Any,
    *,
    owner_user_id: str,
    invoice: dict[str, Any],
) -> dict[str, Any]:
    """Load simulated DB-backed procurement context for a real ingested invoice."""
    match = _invoice_match_values(invoice)
    record = repo.find_ap_context_record(
        owner_user_id=owner_user_id,
        normalized_vendor=match["normalized_vendor"],
        normalized_purchase_order=match["normalized_purchase_order"],
        normalized_invoice_number=match["normalized_invoice_number"],
        amount_due=match["amount_due"],
        issue_date=match["issue_date"],
    )
    if not record:
        return missing_procurement_context(
            "No simulated AP context record matched the parsed vendor, PO, invoice number, amount, or date."
        )
    return procurement_context_from_db_record(record)


def procurement_context_from_db_record(record: Mapping[str, Any]) -> dict[str, Any]:
    context = record.get("context") if isinstance(record.get("context"), Mapping) else {}
    if not context:
        return missing_procurement_context("Matched AP context record did not contain a usable context payload.")

    result = copy.deepcopy(dict(context))
    result["available"] = True
    result.setdefault("schema_version", 1)
    result["scenario"] = result.get("scenario") or record.get("scenario") or "ap_context_record"

    source = result.get("source") if isinstance(result.get("source"), Mapping) else {}
    result["source"] = {
        **dict(source),
        "type": "ap_context_records",
        "record_id": record.get("id"),
        "source_key": record.get("source_key"),
        "match_strategy": record.get("_match_strategy"),
        "metadata": record.get("source_metadata") if isinstance(record.get("source_metadata"), Mapping) else {},
    }
    return result


def summarize_procurement_context(context: dict[str, Any]) -> dict[str, Any]:
    if not context.get("available"):
        return {
            "available": False,
            "reason": context.get("reason"),
            "source": context.get("source"),
        }
    return {
        "available": True,
        "source": context.get("source"),
        "scenario": context.get("scenario"),
        "vendor": context.get("vendor"),
        "purchase_order": context.get("purchase_order"),
        "invoice_total": context.get("invoice_total"),
        "tolerance_policy": context.get("tolerance_policy"),
    }


def iter_ap_context_records_from_manifest(
    manifest: Mapping[str, Any],
    *,
    owner_user_id: str,
    manifest_path: Path | None = None,
) -> list[dict[str, Any]]:
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        return []

    records: list[dict[str, Any]] = []
    for document in documents:
        if not isinstance(document, Mapping):
            continue
        context = _context_from_document(
            document,
            manifest=manifest,
            manifest_path=manifest_path,
        )
        records.append(
            ap_context_record_from_manifest_document(
                document,
                context=context,
                owner_user_id=owner_user_id,
                manifest=manifest,
                manifest_path=manifest_path,
            )
        )
    return records


def ap_context_record_from_manifest_document(
    document: Mapping[str, Any],
    *,
    context: dict[str, Any],
    owner_user_id: str,
    manifest: Mapping[str, Any] | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    seller = document.get("seller") if isinstance(document.get("seller"), Mapping) else {}
    issue_date = document.get("issue_date") if isinstance(document.get("issue_date"), Mapping) else {}
    invoice_total = _first_decimal(
        money_decimal(context.get("invoice_total")),
        _document_amount(document),
    )
    purchase_order = document.get("purchase_order")
    invoice_number = document.get("invoice_number")
    vendor_name = str((context.get("vendor") or {}).get("name") or seller.get("name") or "").strip()
    source_key = ":".join(
        part
        for part in [
            "manifest",
            str(manifest_path.name if manifest_path else (manifest or {}).get("suite") or "generated"),
            str(document.get("document_id") or invoice_number or purchase_order or len(str(document))),
        ]
        if part
    )
    return {
        "owner_user_id": owner_user_id,
        "source_key": source_key,
        "vendor_name": vendor_name,
        "normalized_vendor": normalize_vendor_name(vendor_name),
        "purchase_order": str(purchase_order) if purchase_order else None,
        "normalized_purchase_order": normalize_purchase_order(purchase_order) if purchase_order else None,
        "invoice_number": str(invoice_number) if invoice_number else None,
        "normalized_invoice_number": normalize_invoice_number(invoice_number) if invoice_number else None,
        "invoice_total": normalize_money(invoice_total),
        "issue_date": _date_value(issue_date.get("value")),
        "scenario": context.get("scenario"),
        "context": context,
        "source_metadata": {
            "source": "manifest",
            "manifest_path": str(manifest_path) if manifest_path else None,
            "manifest_schema_version": (manifest or {}).get("schema_version"),
            "suite": (manifest or {}).get("suite"),
            "fixture_slug": (manifest or {}).get("fixture_slug"),
            "document_id": document.get("document_id"),
            "pdf_filename": ((manifest or {}).get("pdf") or {}).get("filename")
            if isinstance((manifest or {}).get("pdf"), Mapping)
            else None,
        },
    }


def load_procurement_context(
    manifest_path: Path,
    *,
    invoice: dict[str, Any] | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return procurement_context_from_manifest(
        manifest,
        invoice=invoice,
        document_id=document_id,
        manifest_path=manifest_path,
    )


def procurement_context_from_manifest(
    manifest: Mapping[str, Any],
    *,
    invoice: dict[str, Any] | None = None,
    document_id: str | None = None,
    manifest_path: Path | None = None,
) -> dict[str, Any]:
    documents = manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        return missing_procurement_context("Manifest does not contain procurement documents.")

    document = _select_document(documents, invoice=invoice, document_id=document_id)
    if document is None:
        return missing_procurement_context("Manifest did not match the parsed invoice.")

    return _context_from_document(
        document,
        manifest=manifest,
        manifest_path=manifest_path,
    )


def _select_document(
    documents: list[Any],
    *,
    invoice: dict[str, Any] | None,
    document_id: str | None,
) -> Mapping[str, Any] | None:
    typed_documents = [document for document in documents if isinstance(document, Mapping)]
    if not typed_documents:
        return None
    if document_id:
        for document in typed_documents:
            if str(document.get("document_id") or "") == document_id:
                return document
        return None
    if invoice:
        scored = [
            (_document_match_score(document, invoice), document)
            for document in typed_documents
        ]
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored and scored[0][0] > 0:
            return scored[0][1]
    return typed_documents[0]


def _document_match_score(document: Mapping[str, Any], invoice: dict[str, Any]) -> int:
    score = 0
    invoice_number = invoice.get("invoice_number") if isinstance(invoice.get("invoice_number"), dict) else {}
    invoice_po = invoice.get("purchase_order") if isinstance(invoice.get("purchase_order"), dict) else {}
    invoice_vendor = invoice.get("vendor") if isinstance(invoice.get("vendor"), dict) else {}
    invoice_amount = invoice.get("amount_due") if isinstance(invoice.get("amount_due"), dict) else {}

    if normalize_invoice_number(invoice_number.get("value")) and normalize_invoice_number(
        invoice_number.get("value")
    ) == normalize_invoice_number(document.get("invoice_number")):
        score += 4
    if normalize_purchase_order(invoice_po.get("value")) and normalize_purchase_order(
        invoice_po.get("value")
    ) == normalize_purchase_order(document.get("purchase_order")):
        score += 3

    document_seller = document.get("seller") if isinstance(document.get("seller"), Mapping) else {}
    if normalize_vendor_name(invoice_vendor.get("name")) and normalize_vendor_name(
        invoice_vendor.get("name")
    ) == normalize_vendor_name(document_seller.get("name")):
        score += 2

    parsed_amount = money_decimal(invoice_amount.get("amount"))
    document_amount = _document_amount(document)
    if parsed_amount is not None and document_amount is not None and abs(parsed_amount - document_amount) <= Decimal("0.01"):
        score += 1
    return score


def _invoice_match_values(invoice: dict[str, Any]) -> dict[str, Any]:
    vendor = invoice.get("vendor") if isinstance(invoice.get("vendor"), dict) else {}
    invoice_number = invoice.get("invoice_number") if isinstance(invoice.get("invoice_number"), dict) else {}
    purchase_order = invoice.get("purchase_order") if isinstance(invoice.get("purchase_order"), dict) else {}
    amount_due = invoice.get("amount_due") if isinstance(invoice.get("amount_due"), dict) else {}
    issue_date = invoice.get("issue_date") if isinstance(invoice.get("issue_date"), dict) else {}
    return {
        "normalized_vendor": vendor.get("normalized_name") or normalize_vendor_name(vendor.get("name")),
        "normalized_purchase_order": purchase_order.get("normalized")
        or normalize_purchase_order(purchase_order.get("value")),
        "normalized_invoice_number": invoice_number.get("normalized")
        or normalize_invoice_number(invoice_number.get("value")),
        "amount_due": money_decimal(amount_due.get("amount")),
        "issue_date": _date_value(issue_date.get("value")),
    }


def _context_from_document(
    document: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    manifest_path: Path | None,
) -> dict[str, Any]:
    ap_context = document.get("ap_context") if isinstance(document.get("ap_context"), Mapping) else {}
    raw_context = ap_context.get("context") if isinstance(ap_context.get("context"), Mapping) else {}
    expected = ap_context.get("expected") if isinstance(ap_context.get("expected"), Mapping) else {}
    seller = document.get("seller") if isinstance(document.get("seller"), Mapping) else {}
    buyer = document.get("buyer") if isinstance(document.get("buyer"), Mapping) else {}
    amounts = document.get("amounts") if isinstance(document.get("amounts"), Mapping) else {}

    invoice_total = _first_decimal(
        _money_from_context(raw_context.get("invoice_total")),
        _document_amount(document),
    )
    po_number = raw_context.get("po_number") or document.get("purchase_order")
    po_authorized_total = _money_from_context(raw_context.get("po_authorized_total"))
    po_remaining = _money_from_context(raw_context.get("po_remaining_before_invoice"))
    po_consumed = _money_from_context(raw_context.get("po_previously_consumed"))

    candidate_open_po = raw_context.get("candidate_open_po") if isinstance(raw_context.get("candidate_open_po"), Mapping) else None
    if candidate_open_po:
        po_number = po_number or candidate_open_po.get("po_number")
        po_authorized_total = _first_decimal(
            po_authorized_total,
            _money_from_context(candidate_open_po.get("authorized_total")),
        )
        po_remaining = _first_decimal(
            po_remaining,
            _money_from_context(candidate_open_po.get("remaining_balance")),
        )

    if po_authorized_total is None:
        po_authorized_total = invoice_total
    if po_remaining is None:
        po_remaining = invoice_total
    if po_consumed is None:
        po_consumed = Decimal("0.00")

    tolerance_percent = _first_decimal(
        _money_from_context(raw_context.get("tolerance_percent")),
        DEFAULT_TOLERANCE_PERCENT,
    )
    tolerance_amount = _money_from_context(raw_context.get("tolerance_amount"))
    if tolerance_amount is None and po_authorized_total is not None:
        tolerance_amount = (po_authorized_total * tolerance_percent / Decimal("100")).quantize(Decimal("0.01"))

    vendor_master = raw_context.get("vendor_master") if isinstance(raw_context.get("vendor_master"), Mapping) else {}
    invoice_payment = raw_context.get("invoice_payment") if isinstance(raw_context.get("invoice_payment"), Mapping) else {}

    previous_invoices = _previous_invoices(raw_context)
    duplicate_candidates = _duplicate_candidates(raw_context)

    return {
        "schema_version": 1,
        "available": True,
        "source": {
            "type": "manifest",
            "path": str(manifest_path) if manifest_path else None,
            "schema_version": manifest.get("schema_version"),
            "suite": manifest.get("suite"),
            "fixture_slug": manifest.get("fixture_slug"),
            "document_id": document.get("document_id"),
            "pdf_filename": (manifest.get("pdf") or {}).get("filename")
            if isinstance(manifest.get("pdf"), Mapping)
            else None,
        },
        "scenario": ap_context.get("scenario") or "manifest_document",
        "vendor": {
            "name": vendor_master.get("vendor_name") or seller.get("name"),
            "normalized_name": normalize_vendor_name(vendor_master.get("vendor_name") or seller.get("name")),
            "approved": True,
        },
        "buyer": {
            "name": buyer.get("name"),
            "normalized_name": normalize_vendor_name(buyer.get("name")),
        },
        "purchase_order": {
            "po_number": str(po_number or ""),
            "normalized": normalize_purchase_order(po_number),
            "authorized_total": normalize_money(po_authorized_total),
            "previously_consumed": normalize_money(po_consumed),
            "remaining_before_invoice": normalize_money(po_remaining),
        },
        "invoice_total": normalize_money(invoice_total),
        "approved_bank_details": {
            "account": vendor_master.get("approved_bank_account"),
        },
        "invoice_payment": {
            "bank_account": invoice_payment.get("bank_account"),
            "remit_to": invoice_payment.get("remit_to"),
        },
        "previous_invoices": previous_invoices,
        "duplicate_candidates": duplicate_candidates,
        "candidate_open_po": _candidate_po(candidate_open_po),
        "tolerance_policy": {
            "percent": normalize_money(tolerance_percent),
            "amount": normalize_money(tolerance_amount),
        },
        "amounts": {
            key: value.get("value")
            for key, value in amounts.items()
            if isinstance(value, Mapping) and value.get("value") is not None
        },
        "expected": dict(expected),
        "raw_ap_context": dict(ap_context),
    }


def _document_amount(document: Mapping[str, Any]) -> Decimal | None:
    amounts = document.get("amounts") if isinstance(document.get("amounts"), Mapping) else {}
    for key in ("balance_due", "total", "subtotal"):
        value = amounts.get(key)
        if isinstance(value, Mapping):
            amount = money_decimal(value.get("value"))
            if amount is not None:
                return amount
    return None


def _money_from_context(value: Any) -> Decimal | None:
    return money_decimal(value)


def _first_decimal(*values: Decimal | None) -> Decimal | None:
    for value in values:
        if value is not None:
            return value
    return None


def _previous_invoices(raw_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    invoices: list[dict[str, Any]] = []
    for key in ("prior_invoice", "previous_related_document"):
        value = raw_context.get(key)
        if isinstance(value, Mapping):
            invoices.append(_invoice_reference(value))
    for database_key in ("client_database", "vendor_database"):
        database = raw_context.get(database_key)
        if not isinstance(database, Mapping):
            continue
        documents = database.get("previous_related_documents")
        if not isinstance(documents, list):
            continue
        for document in documents:
            if isinstance(document, Mapping):
                invoices.append(_invoice_reference(document))
    return _dedupe_references(invoices)


def _duplicate_candidates(raw_context: Mapping[str, Any]) -> list[dict[str, Any]]:
    prior = raw_context.get("prior_invoice")
    if not isinstance(prior, Mapping):
        return []
    return [_invoice_reference(prior)]


def _invoice_reference(value: Mapping[str, Any]) -> dict[str, Any]:
    invoice_number = value.get("invoice_number")
    normalized = value.get("normalized_invoice_number") or normalize_invoice_number(invoice_number)
    return {
        "invoice_number": invoice_number,
        "normalized_invoice_number": str(normalized or ""),
        "vendor_name": value.get("vendor_name"),
        "normalized_vendor": normalize_vendor_name(value.get("vendor_name")),
        "purchase_order": value.get("purchase_order"),
        "normalized_purchase_order": normalize_purchase_order(value.get("purchase_order")),
        "issue_date": value.get("issue_date"),
        "total": normalize_money(_first_value(value.get("total"), value.get("invoice_total"))),
        "applied_to_po": normalize_money(value.get("applied_to_po")),
        "status": value.get("status"),
        "database": value.get("database"),
        "document_id": value.get("document_id")
        or value.get("client_document_id")
        or value.get("vendor_document_id"),
    }


def _dedupe_references(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    result: list[dict[str, Any]] = []
    for value in values:
        key = (
            value.get("database"),
            value.get("document_id"),
            value.get("normalized_invoice_number"),
            value.get("normalized_purchase_order"),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _first_value(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _date_value(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _candidate_po(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not value:
        return None
    return {
        "po_number": value.get("po_number"),
        "normalized": normalize_purchase_order(value.get("po_number")),
        "vendor_name": value.get("vendor_name"),
        "normalized_vendor": normalize_vendor_name(value.get("vendor_name")),
        "authorized_total": normalize_money(value.get("authorized_total")),
        "remaining_balance": normalize_money(value.get("remaining_balance")),
        "service_period": value.get("service_period"),
        "matching_line_description": value.get("matching_line_description"),
    }
