from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any


MONEY_QUANT = Decimal("0.01")
LOW_CONFIDENCE_THRESHOLD = 0.70
CRITICAL_FIELDS = ("vendor", "invoice_number", "issue_date", "amount_due")
MONEY_FIELD_KEYS = (
    "subtotal",
    "discount",
    "tax",
    "shipping",
    "paid",
    "balance_due",
)


def normalize_invoice_parse(parse_result: dict[str, Any]) -> dict[str, Any]:
    fields = parse_result.get("fields") if isinstance(parse_result.get("fields"), dict) else {}
    warnings = [str(warning) for warning in parse_result.get("warnings", []) if warning]
    status = str(parse_result.get("status") or "unknown")

    seller = _party("vendor", fields.get("seller"))
    buyer = _party("buyer", fields.get("buyer"))
    invoice_number = _text_field("invoice_number", fields.get("invoice_number"))
    issue_date = _text_field("issue_date", fields.get("issue_date"))
    due_date = _text_field("due_date", fields.get("due_date"))
    purchase_order = _text_field("purchase_order", fields.get("purchase_order"))
    terms = _text_field("terms", fields.get("terms"))
    currency = _text_field("currency", fields.get("currency"))
    payment = _payment_details(fields.get("payment_instructions"))
    amounts = {
        key: amount
        for key in MONEY_FIELD_KEYS
        if (amount := _money_field(key, fields.get(key))) is not None
    }
    line_items = _line_items(fields.get("line_items"))
    amount_due = amounts.get("balance_due")

    field_scores = _field_scores(
        {
            "vendor": seller,
            "buyer": buyer,
            "invoice_number": invoice_number,
            "issue_date": issue_date,
            "due_date": due_date,
            "purchase_order": purchase_order,
            "terms": terms,
            "currency": currency,
            "amount_due": amount_due,
            "payment_instructions": payment,
            **{f"amounts.{key}": value for key, value in amounts.items()},
        }
    )
    low_confidence_fields = sorted(
        key for key, value in field_scores.items() if value < LOW_CONFIDENCE_THRESHOLD
    )
    missing_fields = _missing_fields(
        {
            "vendor": seller,
            "buyer": buyer,
            "invoice_number": invoice_number,
            "issue_date": issue_date,
            "due_date": due_date,
            "purchase_order": purchase_order,
            "amount_due": amount_due,
        }
    )
    missing_critical_fields = [
        field for field in CRITICAL_FIELDS if field in missing_fields
    ]
    score = _overall_confidence(field_scores, missing_critical_fields, status)

    return {
        "schema_version": 1,
        "source_id": parse_result.get("source_id"),
        "parser_status": status,
        "parser_version": parse_result.get("parser_version"),
        "no_text_layer": status == "no_text_layer",
        "vendor": seller,
        "buyer": buyer,
        "invoice_number": invoice_number,
        "issue_date": issue_date,
        "due_date": due_date,
        "purchase_order": purchase_order,
        "terms": terms,
        "currency": currency,
        "amounts": amounts,
        "amount_due": amount_due,
        "line_items": line_items,
        "bank_details": payment,
        "confidence": {
            "score": score,
            "level": _confidence_level(score),
            "field_scores": field_scores,
        },
        "missing_fields": missing_fields,
        "missing_critical_fields": missing_critical_fields,
        "low_confidence_fields": low_confidence_fields,
        "parse_warnings": warnings,
        "audit": {
            "normalized_invoice_number": invoice_number.get("normalized") if invoice_number else None,
            "normalized_vendor": seller.get("normalized_name") if seller else None,
            "page_count": len(parse_result.get("pages", []))
            if isinstance(parse_result.get("pages"), list)
            else 0,
        },
    }


def normalize_invoice_number(value: Any) -> str:
    text = str(value or "").strip().upper()
    text = re.sub(r"^(?:INVOICE|INV)(?:\s*NO\.?|\s*#)?\s*", "", text)
    canonical = re.sub(r"[^A-Z0-9]+", "", text)
    for prefix in ("INVOICE", "INV"):
        if canonical.startswith(prefix) and len(canonical) > len(prefix):
            canonical = canonical[len(prefix) :]
    if canonical.isdigit():
        canonical = canonical.lstrip("0") or "0"
    return canonical


def normalize_purchase_order(value: Any) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def normalize_vendor_name(value: Any) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [
        token
        for token in text.split()
        if token not in {"llc", "inc", "ltd", "limited", "corp", "corporation", "co", "company"}
    ]
    return " ".join(tokens)


def normalize_money(value: Any) -> str | None:
    amount = money_decimal(value)
    return str(amount) if amount is not None else None


def money_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, dict):
        for key in ("amount", "value"):
            if key in value:
                return money_decimal(value[key])
        return None
    try:
        return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _party(kind: str, field: Any) -> dict[str, Any] | None:
    if not isinstance(field, dict):
        return None
    raw = str(field.get("value") or field.get("raw") or "").strip()
    if not raw:
        return None
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    name = lines[0] if lines else raw
    return {
        "name": name,
        "raw": raw,
        "normalized_name": normalize_vendor_name(name),
        "evidence": _evidence(kind, field),
    }


def _text_field(key: str, field: Any) -> dict[str, Any] | None:
    if not isinstance(field, dict):
        return None
    value = str(field.get("value") or "").strip()
    if not value:
        return None
    result = {
        "value": value,
        "raw": field.get("raw"),
        "evidence": _evidence(key, field),
    }
    if key == "invoice_number":
        result["normalized"] = normalize_invoice_number(value)
    elif key == "purchase_order":
        result["normalized"] = normalize_purchase_order(value)
    return result


def _money_field(key: str, field: Any) -> dict[str, Any] | None:
    if not isinstance(field, dict):
        return None
    amount = money_decimal(field.get("amount", field.get("value")))
    if amount is None:
        return None
    return {
        "amount": str(amount),
        "currency": field.get("currency"),
        "raw": field.get("raw"),
        "evidence": _evidence(key, field),
    }


def _line_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []
    items: list[dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue
        value = item.get("value") if isinstance(item.get("value"), dict) else {}
        amount = _money_field("line_item.amount", item.get("amount"))
        unit_price = _money_field("line_item.unit_price", item.get("unit_price"))
        quantity = item.get("quantity") if isinstance(item.get("quantity"), dict) else None
        description_field = item.get("description") if isinstance(item.get("description"), dict) else None
        description = (
            str(description_field.get("value") or "").strip()
            if description_field
            else str(value.get("description") or item.get("raw") or "").strip()
        )
        items.append(
            {
                "index": index,
                "description": description,
                "quantity": quantity.get("value") if isinstance(quantity, dict) else value.get("quantity"),
                "unit_price": unit_price,
                "amount": amount,
                "currency": value.get("currency") or (amount or {}).get("currency"),
                "raw": item.get("raw"),
                "evidence": {
                    "row": {
                        "page": item.get("page"),
                        "bbox": item.get("bbox"),
                        "raw": item.get("row_raw") or item.get("raw"),
                        "confidence": item.get("confidence"),
                        "method": item.get("method"),
                    },
                    "description": _evidence("line_item.description", description_field)
                    if description_field
                    else None,
                    "quantity": _evidence("line_item.quantity", quantity)
                    if isinstance(quantity, dict)
                    else None,
                    "amount": (amount or {}).get("evidence"),
                },
            }
        )
    return items


def _payment_details(field: Any) -> dict[str, Any] | None:
    if not isinstance(field, dict):
        return None
    raw = str(field.get("value") or field.get("raw") or "").strip()
    if not raw:
        return None
    account = _extract_bank_account(raw)
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", raw)
    return {
        "raw": raw,
        "bank_account": account,
        "remit_to": email_match.group(0) if email_match else None,
        "evidence": _evidence("payment_instructions", field),
    }


def _extract_bank_account(raw: str) -> str | None:
    match = re.search(r"\*{2,}\s*\d{2,}", raw)
    if match:
        return re.sub(r"\s+", " ", match.group(0)).strip()
    match = re.search(r"(?:account|acct|ending)\D{0,12}(\d{4,})", raw, re.IGNORECASE)
    if match:
        return f"**** {match.group(1)[-4:]}"
    return None


def _evidence(source_field: str, field: Any) -> dict[str, Any] | None:
    if not isinstance(field, dict):
        return None
    return {
        "source_field": source_field,
        "page": field.get("page"),
        "bbox": field.get("bbox"),
        "raw": field.get("raw"),
        "label": field.get("label"),
        "confidence": field.get("confidence"),
        "method": field.get("method"),
    }


def _field_scores(fields: dict[str, Any]) -> dict[str, float]:
    scores: dict[str, float] = {}
    for key, value in fields.items():
        if not isinstance(value, dict):
            continue
        evidence = value.get("evidence")
        if not isinstance(evidence, dict):
            continue
        try:
            scores[key] = float(evidence.get("confidence"))
        except (TypeError, ValueError):
            continue
    return scores


def _missing_fields(fields: dict[str, Any]) -> list[str]:
    return sorted(key for key, value in fields.items() if not value)


def _overall_confidence(
    field_scores: dict[str, float],
    missing_critical_fields: list[str],
    status: str,
) -> float:
    if status != "parsed" or missing_critical_fields:
        return 0.0
    critical_scores = [
        field_scores[field]
        for field in ("vendor", "invoice_number", "issue_date", "amount_due")
        if field in field_scores
    ]
    if not critical_scores:
        return 0.0
    return round(sum(critical_scores) / len(critical_scores), 3)


def _confidence_level(score: float) -> str:
    if score >= 0.82:
        return "high"
    if score >= 0.60:
        return "medium"
    return "low"
