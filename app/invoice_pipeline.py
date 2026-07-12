from __future__ import annotations

import hashlib
import json
import math
import platform
import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Iterable


PIPELINE_VERSION = "local-adaptive-v4"
PROFILE_VERSION = 1


@dataclass(frozen=True)
class PageProfile:
    page: int
    route: str
    word_count: int
    visible_char_count: int
    printable_ratio: float
    duplicate_word_ratio: float
    reasons: tuple[str, ...]


def profile_document_pages(
    pages: list[dict[str, Any]],
    words: Iterable[Any],
    *,
    page_char_counts: dict[int, int] | None = None,
) -> list[PageProfile]:
    words_by_page: dict[int, list[Any]] = {}
    for word in words:
        try:
            page = int(getattr(word, "page"))
        except (TypeError, ValueError):
            continue
        words_by_page.setdefault(page, []).append(word)

    profiles: list[PageProfile] = []
    for page_data in pages:
        try:
            page_number = int(page_data["page"])
        except (KeyError, TypeError, ValueError):
            continue
        page_words = words_by_page.get(page_number, [])
        word_text = [str(getattr(word, "text", "") or "").strip() for word in page_words]
        word_text = [text for text in word_text if text]
        visible_chars = sum(len(text) for text in word_text)
        printable_chars = sum(sum(character.isprintable() for character in text) for text in word_text)
        printable_ratio = printable_chars / visible_chars if visible_chars else 0.0
        char_count = (page_char_counts or {}).get(page_number, visible_chars)
        duplicate_ratio = _duplicate_word_ratio(page_words)

        reasons: list[str] = []
        if char_count == 0 or visible_chars < 8 or len(word_text) < 3:
            route = "local_ocr"
            reasons.append("no_usable_native_text")
        else:
            if len(word_text) < 20:
                reasons.append("sparse_native_text")
            if printable_ratio < 0.90:
                reasons.append("garbled_native_text")
            if duplicate_ratio > 0.15:
                reasons.append("duplicate_native_words")
            route = "hybrid" if reasons else "native_text"

        profiles.append(
            PageProfile(
                page=page_number,
                route=route,
                word_count=len(word_text),
                visible_char_count=visible_chars,
                printable_ratio=round(printable_ratio, 3),
                duplicate_word_ratio=round(duplicate_ratio, 3),
                reasons=tuple(reasons),
            )
        )
    return profiles


def document_route(profiles: Iterable[PageProfile], *, ocr_used: bool) -> str:
    routes = {profile.route for profile in profiles}
    if not ocr_used and routes <= {"native_text"}:
        return "native_text"
    if routes == {"local_ocr"}:
        return "local_ocr"
    return "hybrid"


def validate_invoice_fields(fields: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append(_date_order_check(fields))
    checks.append(_amount_composition_check(fields))
    checks.append(_line_item_sum_check(fields))
    checks.append(_currency_consistency_check(fields))
    return checks


def blocking_validation_failures(checks: Iterable[dict[str, Any]]) -> list[str]:
    return [
        str(check.get("id"))
        for check in checks
        if check.get("status") == "fail" and check.get("blocking") is True
    ]


def pipeline_metadata(
    *,
    profiles: list[PageProfile],
    ocr_used: bool,
    validations: list[dict[str, Any]],
    timings_ms: dict[str, float],
    ocr_diagnostics: dict[str, Any] | None = None,
    configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = configuration or {}
    return {
        "version": PIPELINE_VERSION,
        "profile_version": PROFILE_VERSION,
        "route": document_route(profiles, ocr_used=ocr_used),
        "page_profiles": [asdict(profile) for profile in profiles],
        "validations": validations,
        "timings_ms": {key: round(value, 2) for key, value in timings_ms.items()},
        "ocr_diagnostics": ocr_diagnostics or {},
        "configuration_fingerprint": configuration_fingerprint(config),
        "runtime": {
            "python": platform.python_version(),
            "platform": platform.system().lower(),
        },
    }


def configuration_fingerprint(configuration: dict[str, Any]) -> str:
    payload = json.dumps(configuration, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(f"{PIPELINE_VERSION}:{payload}".encode("utf-8")).hexdigest()[:16]


def _duplicate_word_ratio(words: list[Any]) -> float:
    if not words:
        return 0.0
    seen: set[tuple[str, int, int, int, int]] = set()
    duplicates = 0
    for word in words:
        try:
            key = (
                str(getattr(word, "text", "")).casefold(),
                round(float(getattr(word, "x0"))),
                round(float(getattr(word, "top"))),
                round(float(getattr(word, "x1"))),
                round(float(getattr(word, "bottom"))),
            )
        except (TypeError, ValueError):
            continue
        if key in seen:
            duplicates += 1
        seen.add(key)
    return duplicates / len(words)


def _date_order_check(fields: dict[str, Any]) -> dict[str, Any]:
    issue_date = _field_value(fields.get("issue_date"))
    due_date = _field_value(fields.get("due_date"))
    if not issue_date or not due_date:
        return _check("date_order", "skipped", blocking=False)
    try:
        issue = date.fromisoformat(str(issue_date))
        due = date.fromisoformat(str(due_date))
    except ValueError:
        return _check("date_order", "fail", blocking=True, reason="invalid_normalized_date")
    if due < issue:
        return _check(
            "date_order",
            "fail",
            blocking=True,
            reason="due_date_precedes_issue_date",
            observed={"issue_date": str(issue_date), "due_date": str(due_date)},
        )
    return _check("date_order", "pass", blocking=False)


def _amount_composition_check(fields: dict[str, Any]) -> dict[str, Any]:
    subtotal = _amount(fields.get("subtotal"))
    balance_due = _amount(fields.get("balance_due"))
    if subtotal is None or balance_due is None:
        return _check("amount_composition", "skipped", blocking=False)
    discount = _amount(fields.get("discount")) or 0.0
    tax = _amount(fields.get("tax")) or 0.0
    shipping = _amount(fields.get("shipping")) or 0.0
    paid = _amount(fields.get("paid")) or 0.0
    expected = subtotal - abs(discount) + tax + shipping - paid
    component_fields = [
        fields.get(key)
        for key in ("subtotal", "discount", "tax", "shipping", "paid", "balance_due")
        if isinstance(fields.get(key), dict)
    ]
    rounding_tolerance = sum(_display_increment(field) / 2 for field in component_fields)
    tolerance = max(0.02, abs(balance_due) * 0.001, rounding_tolerance)
    if not math.isclose(expected, balance_due, abs_tol=tolerance):
        return _check(
            "amount_composition",
            "fail",
            blocking=True,
            reason="balance_due_does_not_reconcile",
            observed={"expected": round(expected, 2), "balance_due": round(balance_due, 2)},
        )
    return _check("amount_composition", "pass", blocking=False)


def _line_item_sum_check(fields: dict[str, Any]) -> dict[str, Any]:
    items = fields.get("line_items")
    subtotal = _amount(fields.get("subtotal"))
    if not isinstance(items, list) or not items or subtotal is None:
        return _check("line_item_sum", "skipped", blocking=False)
    amounts = [_amount(item.get("amount")) for item in items if isinstance(item, dict)]
    known_amounts = [amount for amount in amounts if amount is not None]
    if len(known_amounts) != len(items):
        return _check("line_item_sum", "skipped", blocking=False, reason="incomplete_line_amounts")
    item_sum = sum(known_amounts)
    rounding_tolerance = sum(
        _display_increment(item.get("amount")) / 2
        for item in items
        if isinstance(item, dict)
    ) + _display_increment(fields.get("subtotal")) / 2
    tolerance = max(0.02, abs(subtotal) * 0.001, rounding_tolerance)
    if not math.isclose(item_sum, subtotal, abs_tol=tolerance):
        return _check(
            "line_item_sum",
            "fail",
            blocking=True,
            reason="line_items_do_not_match_subtotal",
            observed={"line_item_sum": round(item_sum, 2), "subtotal": round(subtotal, 2)},
        )
    return _check("line_item_sum", "pass", blocking=False)


def _currency_consistency_check(fields: dict[str, Any]) -> dict[str, Any]:
    expected = str(_field_value(fields.get("currency")) or "").upper()
    if not expected:
        return _check("currency_consistency", "skipped", blocking=False)
    observed: set[str] = set()
    for key in ("subtotal", "discount", "tax", "shipping", "paid", "balance_due"):
        value = fields.get(key)
        if isinstance(value, dict) and isinstance(value.get("currency"), str):
            observed.add(value["currency"].upper())
    for item in fields.get("line_items") or []:
        if not isinstance(item, dict):
            continue
        for key in ("unit_price", "amount"):
            value = item.get(key)
            if isinstance(value, dict) and isinstance(value.get("currency"), str):
                observed.add(value["currency"].upper())
    observed.discard("")
    conflicts = sorted(currency for currency in observed if currency != expected)
    if conflicts:
        return _check(
            "currency_consistency",
            "fail",
            blocking=True,
            reason="conflicting_currency_evidence",
            observed={"document_currency": expected, "conflicts": conflicts},
        )
    return _check("currency_consistency", "pass", blocking=False)


def _field_value(value: Any) -> Any:
    return value.get("value") if isinstance(value, dict) else None


def _amount(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("amount", value.get("value"))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _display_increment(value: Any) -> float:
    if not isinstance(value, dict):
        return 0.01
    raw = str(value.get("raw") or "").strip()
    decimal_match = re.search(r"[.,](\d{1,2})\)?$", raw)
    if decimal_match:
        return 0.1 if len(decimal_match.group(1)) == 1 else 0.01
    return 1.0


def _check(
    check_id: str,
    status: str,
    *,
    blocking: bool,
    reason: str | None = None,
    observed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"id": check_id, "status": status, "blocking": blocking}
    if reason:
        result["reason"] = reason
    if observed:
        result["observed"] = observed
    return result
