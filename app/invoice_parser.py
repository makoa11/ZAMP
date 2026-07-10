from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


PARSER_VERSION = "static-pdf-v1"

FIELD_KEYS = (
    "invoice_number",
    "issue_date",
    "due_date",
    "purchase_order",
    "terms",
    "currency",
    "seller",
    "buyer",
    "subtotal",
    "discount",
    "tax",
    "shipping",
    "paid",
    "balance_due",
    "payment_instructions",
)

LABELS: dict[str, tuple[str, ...]] = {
    "invoice_number": (
        "invoice number",
        "invoice no",
        "invoice #",
        "bill id",
        "bill no",
        "doc #",
        "document number",
        "voucher no",
        "reference",
        "note ref",
        "receipt id",
        "account ref",
        "inv ref",
        "request no",
        "serial",
        "ticket",
        "batch id",
        "folio",
        "no.",
    ),
    "issue_date": (
        "invoice date",
        "issue date",
        "issued",
        "bill dt",
        "bill date",
        "date",
        "raised on",
        "posting date",
        "dated",
        "created",
        "printed",
        "account date",
        "inv date",
        "request date",
        "doc date",
        "work date",
        "batch date",
        "fecha",
    ),
    "due_date": (
        "due date",
        "due",
        "pay by",
        "payment before",
        "settle by",
        "collection date",
        "last date",
        "clear by",
        "balance on",
        "payment target",
        "collection",
        "needed by",
        "cash date",
        "payment cutoff",
        "release date",
        "limite pago",
    ),
    "purchase_order": (
        "purchase order",
        "po number",
        "po ref",
        "po",
        "order ref",
        "buyer ref",
        "client order",
        "contract",
        "job no",
        "auth ref",
        "ref code",
        "work ref",
        "budget ref",
        "batch",
        "site order",
        "control",
        "orden",
    ),
    "terms": (
        "terms",
        "pay terms",
        "payment terms",
        "agreement",
        "credit",
        "settlement",
        "credit days",
        "window",
        "policy",
        "basis",
        "payment rule",
        "cycle",
        "contract term",
        "run terms",
        "condiciones",
    ),
    "seller": (
        "from",
        "seller",
        "supplier",
        "provider",
        "remit from",
        "issuer",
        "prepared by",
        "merchant",
        "origin",
        "vendor",
        "payee",
        "consignor",
        "entity",
        "contractor",
        "source",
        "emisor",
    ),
    "buyer": (
        "bill to",
        "billed to",
        "customer",
        "client",
        "account",
        "to",
        "charged to",
        "payer",
        "destination",
        "recipient",
        "consignee",
        "requester",
        "counterparty",
        "site",
        "payor",
        "receptor",
    ),
    "subtotal": (
        "subtotal",
        "goods value",
        "billed amount",
        "net services",
        "taxable value",
        "charge total",
        "debit total",
        "items total",
        "value before tax",
        "assessable",
        "requested amount",
        "sheet total",
        "work value",
        "batch sum",
    ),
    "discount": (
        "discount",
        "less disc",
        "less discount",
        "allowance",
        "rebate",
        "deduction",
        "adjustment",
        "credit adj",
        "promo",
        "offset",
        "scheme disc",
        "withheld",
        "less",
        "retention",
        "holdback",
        "descuento",
    ),
    "tax": (
        "tax",
        "gst",
        "sales tax",
        "vat",
        "tax charged",
        "cgst sgst",
        "tax add-on",
        "consumption tax",
        "iva",
    ),
    "shipping": (
        "shipping",
        "freight",
        "handling",
        "expenses",
        "carriage",
        "delivery",
        "other fees",
        "service",
        "logistics",
        "transport",
        "pass-through",
        "move cost",
        "travel",
        "admin",
        "envio",
    ),
    "paid": (
        "paid",
        "received",
        "payment received",
        "advance",
        "settled",
        "credit",
        "already paid",
        "applied",
        "tendered",
        "deposit",
        "released",
        "cleared",
        "progress pay",
        "abonado",
    ),
    "balance_due": (
        "balance due",
        "amount due",
        "total due",
        "left balance",
        "remaining payment",
        "open amount",
        "amount open",
        "left to pay",
        "outstanding",
        "amount left",
        "net due",
        "receivable",
        "unreleased",
        "not cleared",
        "final claim",
        "pending release",
        "saldo",
    ),
    "payment_instructions": (
        "payment instructions",
        "payment",
        "bank details",
        "settlement",
        "remittance",
        "payable to",
        "payment route",
        "transfer info",
        "tender",
        "funds",
        "upi bank",
        "bank",
        "eft detail",
        "clearing",
        "forma de pago",
        "remit",
    ),
}

TABLE_DESCRIPTION_LABELS = (
    "item",
    "particulars",
    "description",
    "work",
    "charge",
    "narration",
    "memo",
    "line",
    "supply",
    "billing description",
    "material",
    "task",
    "details",
    "concept",
)

TABLE_AMOUNT_LABELS = (
    "amount",
    "amt",
    "billed amt",
    "line total",
    "net",
    "taxable",
    "payable",
    "debit",
    "ext",
    "value",
    "tax inv val",
    "charge",
    "rmb amt",
    "due amt",
    "jpy",
    "importe",
)

TABLE_QUANTITY_LABELS = (
    "qty",
    "quantity",
    "count",
    "units",
    "unit",
    "hours",
    "nos",
    "pack",
    "time",
)

TABLE_UNIT_PRICE_LABELS = (
    "rate",
    "price",
    "unit fee",
    "basic",
    "each",
)

TABLE_OTHER_LABELS = (
    "#",
    "ln",
    "sku",
    "code",
    "hsn",
    "hsn sac",
    "hsn/sac",
    "service date",
    "txn date",
    "applied",
    "ref",
    "job",
)

AMBIGUOUS_PARTY_LABELS = {"account", "to", "site", "entity", "source"}
PARTY_VALUE_NOISE_TOKENS = {
    "date",
    "order",
    "ref",
    "reference",
    "number",
    "no",
    "id",
    "total",
    "subtotal",
    "amount",
    "value",
    "charge",
    "tax",
    "rate",
    "qty",
    "quantity",
}
NON_ITEM_ROW_START_LABELS = (
    "payment instructions",
    "payment",
    "bank details",
    "settlement",
    "remittance",
    "terms",
    "notes",
    "note",
    "footer",
    "page",
    "thank you",
)
ISO_CURRENCY_CODES = ("USD", "INR", "EUR", "GBP", "AED", "SGD", "CAD", "AUD", "CNY", "JPY", "ZAR", "MXN")
SYMBOL_CURRENCY_MAP = {
    "US$": "USD",
    "$": "USD",
    "Rs": "INR",
    "Rs.": "INR",
    "€": "EUR",
    "£": "GBP",
    "S$": "SGD",
    "C$": "CAD",
    "A$": "AUD",
    "RMB": "CNY",
    "R": "ZAR",
    "Mex$": "MXN",
}
CURRENCY_PATTERN = (
    r"USD|INR|EUR|GBP|AED|SGD|CAD|AUD|CNY|JPY|ZAR|MXN|RMB|Mex\$|US\$|S\$|C\$|A\$|Rs\.?|[$€£¥YR]"
)
NUMBER_PATTERN = r"\(?[-+]?\d(?:[\d\s,.'’]*\d)?(?:[.,]\d{1,2})?\)?"
MONEY_RE = re.compile(
    rf"(?P<prefix>{CURRENCY_PATTERN})?\s*(?P<number>{NUMBER_PATTERN})\s*(?P<suffix>{CURRENCY_PATTERN})?",
    re.IGNORECASE,
)

MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}


@dataclass(frozen=True)
class Word:
    text: str
    page: int
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class Line:
    page: int
    words: tuple[Word, ...]
    text: str
    x0: float
    top: float
    x1: float
    bottom: float


@dataclass(frozen=True)
class LabelMatch:
    label: str
    start: int
    end: int


@dataclass(frozen=True)
class MoneyMention:
    raw: str
    amount: float
    currency: str | None
    span: tuple[int, int]


@dataclass(frozen=True)
class MoneyWordSpan:
    words: tuple[Word, ...]
    money: MoneyMention


@dataclass(frozen=True)
class HeaderLabel:
    label: str
    roles: frozenset[str]
    start: int
    end: int
    x0: float
    x1: float


@dataclass(frozen=True)
class TableColumn:
    role: str
    label: str
    x0: float
    x1: float
    range_x0: float
    range_x1: float


@dataclass(frozen=True)
class TableHeader:
    page: int
    line: Line
    columns: tuple[TableColumn, ...]
    region_x0: float
    region_x1: float


@dataclass(frozen=True)
class WordTableRow:
    page: int
    words: tuple[Word, ...]
    cells: dict[str, tuple[Word, ...]]
    raw: str
    bbox: list[float]


def parse_invoice_pdf(content: bytes, *, source_id: str | None = None) -> dict[str, Any]:
    warnings: list[str] = []
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError:
        return _empty_result(
            "unsupported",
            warnings=["Install pdfplumber to enable static PDF invoice parsing."],
            source_id=source_id,
        )

    pages: list[dict[str, Any]] = []
    words: list[Word] = []
    char_count = 0

    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text(x_tolerance=1.5, y_tolerance=3) or ""
                pages.append(
                    {
                        "page": page_index,
                        "width": float(page.width),
                        "height": float(page.height),
                        "text": page_text,
                    }
                )
                page_chars = getattr(page, "chars", []) or []
                char_count += len(page_chars)
                for raw_word in page.extract_words(
                    x_tolerance=1.5,
                    y_tolerance=3,
                    keep_blank_chars=False,
                    use_text_flow=False,
                ):
                    text = str(raw_word.get("text") or "").strip()
                    if not text:
                        continue
                    words.append(
                        Word(
                            text=text,
                            page=page_index,
                            x0=float(raw_word["x0"]),
                            top=float(raw_word["top"]),
                            x1=float(raw_word["x1"]),
                            bottom=float(raw_word["bottom"]),
                        )
                    )
    except Exception as exc:
        return _empty_result(
            "failed",
            pages=pages,
            warnings=[f"PDF parsing failed: {exc}"],
            source_id=source_id,
        )

    if _has_no_text_layer(char_count, words):
        return _empty_result(
            "no_text_layer",
            pages=pages,
            warnings=["PDF has no usable text layer; OCR is not attempted by this parser."],
            source_id=source_id,
        )

    lines = _build_lines(words)
    fields = _extract_fields(lines=lines, words=words, warnings=warnings)
    _add_missing_warnings(fields, warnings)
    result = {
        "status": "parsed",
        "parser_version": PARSER_VERSION,
        "fields": fields,
        "pages": pages,
        "warnings": warnings,
    }
    if source_id:
        result["source_id"] = source_id
    return result


def _empty_result(
    status: str,
    *,
    pages: list[dict[str, Any]] | None = None,
    warnings: list[str] | None = None,
    source_id: str | None = None,
) -> dict[str, Any]:
    fields = {key: None for key in FIELD_KEYS}
    fields["line_items"] = []
    result = {
        "status": status,
        "parser_version": PARSER_VERSION,
        "fields": fields,
        "pages": pages or [],
        "warnings": warnings or [],
    }
    if source_id:
        result["source_id"] = source_id
    return result


def _has_no_text_layer(char_count: int, words: list[Word]) -> bool:
    visible_chars = sum(len(word.text.strip()) for word in words)
    return char_count == 0 or visible_chars < 8 or len(words) < 3


def _build_lines(words: list[Word]) -> list[Line]:
    lines: list[Line] = []
    by_page: dict[int, list[Word]] = {}
    for word in words:
        by_page.setdefault(word.page, []).append(word)

    for page, page_words in sorted(by_page.items()):
        sorted_words = sorted(page_words, key=lambda item: (item.top, item.x0))
        groups: list[list[Word]] = []
        for word in sorted_words:
            if not groups:
                groups.append([word])
                continue
            current = groups[-1]
            current_mid = sum((item.top + item.bottom) / 2 for item in current) / len(current)
            word_mid = (word.top + word.bottom) / 2
            tolerance = max(3.2, (word.bottom - word.top) * 0.55)
            if abs(word_mid - current_mid) <= tolerance:
                current.append(word)
            else:
                groups.append([word])

        for group in groups:
            ordered = tuple(sorted(group, key=lambda item: item.x0))
            text = " ".join(word.text for word in ordered).strip()
            if not text:
                continue
            lines.append(
                Line(
                    page=page,
                    words=ordered,
                    text=text,
                    x0=min(word.x0 for word in ordered),
                    top=min(word.top for word in ordered),
                    x1=max(word.x1 for word in ordered),
                    bottom=max(word.bottom for word in ordered),
                )
            )

    return sorted(lines, key=lambda item: (item.page, item.top, item.x0))


def _extract_fields(
    *,
    lines: list[Line],
    words: list[Word],
    warnings: list[str],
) -> dict[str, Any]:
    fields: dict[str, Any] = {key: None for key in FIELD_KEYS}
    currency = _infer_currency(lines)
    if currency:
        fields["currency"] = _field_value(
            raw=currency["raw"],
            value=currency["value"],
            page=currency["page"],
            bbox=currency["bbox"],
            label="currency",
            confidence=currency["confidence"],
            method=currency["method"],
        )

    for key in ("invoice_number", "issue_date", "due_date", "purchase_order", "terms"):
        fields[key] = _best_scalar_field(lines, key)

    fields["seller"] = _party_field(lines, "seller")
    fields["buyer"] = _party_field(lines, "buyer")
    fields["payment_instructions"] = _payment_field(lines)

    inferred_currency = fields["currency"]["value"] if isinstance(fields.get("currency"), dict) else None
    for key in ("subtotal", "discount", "tax", "shipping", "paid", "balance_due"):
        fields[key] = _best_money_field(lines, key, inferred_currency)

    fields["line_items"] = _line_items_from_word_tables(words, lines, inferred_currency)
    _add_line_item_total_warnings(fields, warnings)

    return fields


def _best_scalar_field(lines: list[Line], key: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for line in lines:
        match = _match_any_label(line, LABELS[key])
        if not match:
            continue
        raw, bbox, method = _value_after_label(lines, line, match, allow_below=True)
        raw = _clean_field_raw(raw)
        if key in {"issue_date", "due_date"}:
            date_match = _parse_date(raw)
            if not date_match:
                date_match = _parse_date(line.text)
            if not date_match:
                continue
            raw_value, normalized = date_match
            raw = raw_value
            value = normalized
        else:
            raw = _trim_at_known_label(raw)
            value = _normalize_scalar_value(key, raw)
            if not value:
                continue

        confidence = 0.92 if method == "same_line" else 0.78
        if len(_label_tokens(match.label)) >= 2:
            confidence += 0.03
        if key == "invoice_number" and _looks_like_invoice_number(str(value)):
            confidence += 0.03
        candidates.append(
            _field_value(
                raw=raw,
                value=value,
                page=line.page,
                bbox=bbox,
                label=match.label,
                confidence=min(confidence, 0.98),
                method=f"label_{method}",
            )
        )

    return _best_candidate(candidates)


def _best_money_field(lines: list[Line], key: str, inferred_currency: str | None) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for line in lines:
        match = _match_any_label(line, LABELS[key])
        if not match:
            if key == "balance_due":
                match = _generic_total_match(line)
            if not match:
                continue
        word_candidate = _money_field_after_label_words(
            line,
            match,
            key=key,
            inferred_currency=inferred_currency,
        )
        if word_candidate:
            candidates.append(word_candidate)
            continue

        raw, bbox, method = _value_after_label(lines, line, match, allow_below=False)
        money_values = _parse_money_values(raw) or _parse_money_values(line.text)
        if not money_values:
            continue
        money = money_values[-1]
        currency = _currency_for_money(money, inferred_currency)
        confidence = 0.9 if method == "same_line" else 0.74
        if key == "balance_due" and _canonical_label(match.label) in {"total"}:
            confidence = 0.72
        if _is_bottom_or_right(line):
            confidence += 0.04
        candidates.append(
            _money_field(
                money=money,
                currency=currency,
                page=line.page,
                bbox=bbox,
                label=match.label,
                confidence=min(confidence, 0.98),
                method=f"money_label_{method}",
            )
        )
    return _best_candidate(candidates)


def _generic_total_match(line: Line) -> LabelMatch | None:
    tokens = [_canonical_token(word.text) for word in line.words]
    for index, token in enumerate(tokens):
        if token == "total":
            return LabelMatch("total", index, index + 1)
    return None


def _money_field_after_label_words(
    line: Line,
    match: LabelMatch,
    *,
    key: str,
    inferred_currency: str | None,
) -> dict[str, Any] | None:
    words = tuple(word for word in line.words[match.end :] if _canonical_token(word.text) or _is_currency_word(word.text))
    span = _rightmost_money_span(words)
    if span is None:
        return None
    confidence = 0.92
    if key == "balance_due" and _canonical_label(match.label) in {"total"}:
        confidence = 0.76
    if _is_bottom_or_right(line):
        confidence += 0.04
    return _money_field(
        money=span.money,
        currency=_currency_for_money(span.money, inferred_currency),
        page=line.page,
        bbox=_bbox_for_words(span.words),
        label=match.label,
        confidence=min(confidence, 0.98),
        method="money_label_words",
    )


def _party_field(lines: list[Line], key: str) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for line in lines:
        match = _match_party_label(line, key)
        if not match:
            continue
        block_left = max(0.0, line.words[match.start].x0 - 8.0)
        block_right = _party_block_right_boundary(line, match, lines)
        label_line = _line_from_words(line.page, line.words[match.start : match.end])
        collected: list[Line] = []
        same_line = tuple(
            word
            for word in line.words[match.end :]
            if _canonical_token(word.text) and word.x0 >= block_left and word.x0 < block_right
        )
        if same_line:
            collected.append(_line_from_words(line.page, same_line))
        for candidate in _following_lines(lines, line, max_gap=72, max_lines=5):
            cropped_words = _party_block_words(candidate, left=block_left, right=block_right)
            if not cropped_words:
                continue
            cropped_line = _line_from_words(candidate.page, cropped_words)
            if _match_party_label(cropped_line, key):
                break
            if _matches_party_stop_label(cropped_line, key) or _is_table_header(cropped_line):
                break
            if abs(cropped_line.x0 - line.x0) > 42 and not _horizontally_overlaps(cropped_line, label_line):
                continue
            collected.append(cropped_line)
        raw = "\n".join(item.text for item in collected).strip()
        if not raw:
            continue
        if _is_low_quality_party_candidate(match, collected):
            continue
        bbox = _merge_bboxes([label_line, *collected])
        confidence = 0.8 if _is_ambiguous_party_label(match.label) else 0.86
        if len(collected) > 1:
            confidence += 0.03
        candidates.append(
            _field_value(
                raw=raw,
                value=raw,
                page=line.page,
                bbox=bbox,
                label=match.label,
                confidence=min(confidence, 0.92),
                method="party_block",
            )
        )

    if candidates:
        return _best_candidate(candidates)
    if key == "seller":
        return _seller_fallback(lines)
    return None


def _seller_fallback(lines: list[Line]) -> dict[str, Any] | None:
    for line in lines:
        if line.top > 160 or _matches_any_known_label(line):
            continue
        text = line.text.strip()
        if len(text) < 3 or _canonical_label(text) in {"invoice", "taxinvoice", "statement", "receipt"}:
            continue
        return _field_value(
            raw=text,
            value=text,
            page=line.page,
            bbox=_bbox(line),
            label=None,
            confidence=0.45,
            method="top_page_fallback",
        )
    return None


def _match_party_label(line: Line, key: str) -> LabelMatch | None:
    matches = [
        match
        for match in _label_matches(line, LABELS[key])
        if _is_party_header_match(line, match)
        and not _is_shadowed_by_longer_non_party_label(line, match)
    ]
    if not matches:
        return None
    return min(
        matches,
        key=lambda item: (
            item.start,
            -(item.end - item.start),
            _party_label_priority(item.label),
        ),
    )


def _label_matches(line: Line, labels: Iterable[str]) -> list[LabelMatch]:
    tokens = [_canonical_token(word.text) for word in line.words]
    matches: list[LabelMatch] = []
    for label in labels:
        label_tokens = _label_tokens(label)
        if not label_tokens:
            continue
        for index in range(0, len(tokens) - len(label_tokens) + 1):
            if tokens[index : index + len(label_tokens)] == label_tokens:
                matches.append(LabelMatch(label, index, index + len(label_tokens)))
    return matches


def _is_shadowed_by_longer_non_party_label(line: Line, match: LabelMatch) -> bool:
    party_labels = set(LABELS["seller"]) | set(LABELS["buyer"])
    for key, labels in LABELS.items():
        if key in {"seller", "buyer"}:
            continue
        for other in _label_matches(line, labels):
            if other.start != match.start or other.end <= match.end:
                continue
            if other.label not in party_labels:
                return True
    return False


def _party_label_priority(label: str) -> int:
    return 1 if _is_ambiguous_party_label(label) else 0


def _is_ambiguous_party_label(label: str) -> bool:
    return _canonical_label(label) in AMBIGUOUS_PARTY_LABELS


def _is_low_quality_party_candidate(match: LabelMatch, collected: list[Line]) -> bool:
    if not _is_ambiguous_party_label(match.label):
        return False
    raw = " ".join(line.text for line in collected).strip()
    if not raw:
        return True
    tokens = [_canonical_token(word.text) for line in collected for word in line.words if _canonical_token(word.text)]
    if not tokens:
        return True
    if tokens[0] in PARTY_VALUE_NOISE_TOKENS:
        return True
    if _parse_date(raw) or _matches_total_text(raw):
        return True
    if _parse_money_values(raw) and len(tokens) <= 4:
        return True
    alpha_chars = len(re.sub(r"[^A-Za-z]", "", raw))
    digit_chars = len(re.sub(r"\D", "", raw))
    if alpha_chars < 3 or (digit_chars > alpha_chars and len(tokens) <= 3):
        return True
    if len(tokens) == 1 and len(tokens[0]) <= 3:
        return True
    return False


def _is_party_header_match(line: Line, match: LabelMatch) -> bool:
    if not line.words:
        return False
    if match.start == 0:
        return line.words[match.start].x0 - line.x0 <= 3.0
    previous = line.words[match.start - 1]
    current = line.words[match.start]
    return current.x0 - previous.x1 > 72.0 and current.x0 - line.x0 > 80.0


def _party_block_right_boundary(line: Line, match: LabelMatch, lines: list[Line]) -> float:
    boundary = _first_large_word_gap_boundary(line.words[match.end - 1 :])
    if boundary is not None:
        return boundary

    for candidate in _following_lines(lines, line, max_gap=72, max_lines=5):
        if abs(candidate.x0 - line.x0) > 8:
            continue
        boundary = _first_large_word_gap_boundary(candidate.words)
        if boundary is not None:
            return boundary

    page_right = max((candidate.x1 for candidate in lines if candidate.page == line.page), default=line.x1)
    return min(line.x0 + 260.0, page_right)


def _first_large_word_gap_boundary(words: tuple[Word, ...]) -> float | None:
    if len(words) < 2:
        return None
    ordered = tuple(sorted(words, key=lambda item: item.x0))
    for previous, current in zip(ordered, ordered[1:]):
        gap = current.x0 - previous.x1
        if gap > 72 and current.x0 - ordered[0].x0 > 110:
            return (previous.x1 + current.x0) / 2
    return None


def _party_block_words(line: Line, *, left: float, right: float) -> tuple[Word, ...]:
    return tuple(
        word
        for word in line.words
        if word.x0 >= left and _word_mid_x(word) < right
    )


def _matches_party_stop_label(line: Line, key: str) -> bool:
    if key == "seller" and _match_line_start_label(line, LABELS["buyer"]):
        return True
    stop_keys = (
        "invoice_number",
        "issue_date",
        "due_date",
        "purchase_order",
        "terms",
        "payment_instructions",
        "subtotal",
        "discount",
        "tax",
        "shipping",
        "paid",
        "balance_due",
    )
    return any(_match_line_start_label(line, LABELS[stop_key]) for stop_key in stop_keys)


def _match_line_start_label(line: Line, labels: Iterable[str]) -> LabelMatch | None:
    matches = [match for match in _label_matches(line, labels) if match.start == 0]
    if not matches:
        return None
    return max(matches, key=lambda item: item.end - item.start)


def _payment_field(lines: list[Line]) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []
    for line in lines:
        match = _match_any_label(line, LABELS["payment_instructions"])
        if not match:
            continue
        collected: list[Line] = []
        same_line = tuple(word for word in line.words[match.end :] if _canonical_token(word.text))
        if same_line:
            collected.append(_line_from_words(line.page, same_line))
        for candidate in _following_lines(lines, line, max_gap=54, max_lines=4):
            if _matches_any_total_label(candidate) or _is_table_header(candidate):
                break
            if abs(candidate.x0 - line.x0) > 50 and not _horizontally_overlaps(candidate, line):
                continue
            collected.append(candidate)
        raw = "\n".join(item.text for item in collected).strip()
        if not raw:
            continue
        candidates.append(
            _field_value(
                raw=raw,
                value=raw,
                page=line.page,
                bbox=_merge_bboxes([line, *collected]),
                label=match.label,
                confidence=0.82,
                method="payment_block",
            )
        )
    return _best_candidate(candidates)


def _infer_currency(lines: list[Line]) -> dict[str, Any] | None:
    counts: dict[str, tuple[int, Line, str]] = {}
    for line in lines:
        for mention in _parse_money_values(line.text):
            if not mention.currency:
                continue
            count, first_line, raw = counts.get(mention.currency, (0, line, mention.raw))
            counts[mention.currency] = (count + 1, first_line, raw)
    if not counts:
        for line in lines:
            for code in ISO_CURRENCY_CODES:
                if re.search(rf"\b{re.escape(code)}\b", line.text, re.IGNORECASE):
                    return {
                        "raw": code,
                        "value": code,
                        "page": line.page,
                        "bbox": _bbox(line),
                        "confidence": 0.58,
                        "method": "currency_code_text",
                    }
            if re.search(r"\bRMB\b", line.text, re.IGNORECASE):
                return {
                    "raw": "RMB",
                    "value": "CNY",
                    "page": line.page,
                    "bbox": _bbox(line),
                    "confidence": 0.58,
                    "method": "currency_alias_text",
                }
        return None
    currency, (count, line, raw) = max(counts.items(), key=lambda item: item[1][0])
    return {
        "raw": raw,
        "value": currency,
        "page": line.page,
        "bbox": _bbox(line),
        "confidence": min(0.72 + count * 0.03, 0.93),
        "method": "money_currency_frequency",
    }


def _line_items_from_word_tables(
    words: list[Word],
    lines: list[Line],
    inferred_currency: str | None,
) -> list[dict[str, Any]]:
    headers = [header for line in lines if (header := _infer_table_header(line, lines))]
    headers = _headers_with_continuations(headers, words)
    table_groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for header in headers:
        items = _items_for_table_header(words, header, inferred_currency)
        if items:
            table_groups.setdefault(_table_schema_signature(header), []).extend(items)
    if not table_groups:
        return []
    best_items = max(
        (_dedupe_line_items(items) for items in table_groups.values()),
        key=lambda items: (
            len(items),
            len({item.get("page") for item in items}),
            sum(float(item.get("confidence") or 0) for item in items),
        ),
    )
    return sorted(best_items, key=lambda item: (int(item.get("page") or 0), (item.get("bbox") or [0, 0])[1]))


def _headers_with_continuations(headers: list[TableHeader], words: list[Word]) -> list[TableHeader]:
    if not headers:
        return []
    result = list(headers)
    explicit_pages = {header.page for header in headers}
    pages = sorted({word.page for word in words})
    max_page = max(pages, default=0)
    for header in sorted(headers, key=lambda item: (item.page, item.line.top)):
        next_header_page = min((item.page for item in headers if item.page > header.page), default=max_page + 1)
        for page in pages:
            if page <= header.page or page >= next_header_page or page in explicit_pages:
                continue
            continuation = _continued_table_header(header, page)
            if _items_for_table_header(words, continuation, inferred_currency=None):
                result.append(continuation)
    return sorted(result, key=lambda item: (item.page, item.line.top, item.region_x0))


def _continued_table_header(header: TableHeader, page: int) -> TableHeader:
    line = Line(
        page=page,
        words=(),
        text="",
        x0=header.line.x0,
        top=0.0,
        x1=header.line.x1,
        bottom=0.0,
    )
    return TableHeader(
        page=page,
        line=line,
        columns=header.columns,
        region_x0=header.region_x0,
        region_x1=header.region_x1,
    )


def _table_schema_signature(header: TableHeader) -> tuple[str, ...]:
    return tuple(column.role for column in header.columns)


def _dedupe_line_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    deduped: list[dict[str, Any]] = []
    for item in sorted(items, key=lambda value: (int(value.get("page") or 0), (value.get("bbox") or [0, 0])[1])):
        amount = item.get("amount") if isinstance(item.get("amount"), dict) else {}
        key = (
            item.get("page"),
            tuple(round(float(coord), 1) for coord in (item.get("bbox") or [])),
            item.get("raw"),
            amount.get("amount") if isinstance(amount, dict) else None,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _items_for_table_header(
    words: list[Word],
    header: TableHeader,
    inferred_currency: str | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    pending: WordTableRow | None = None
    previous_bottom = header.line.bottom

    for row in _table_rows_from_header(words, header):
        gap = row.bbox[1] - previous_bottom
        if (items or pending) and gap > 58:
            if pending:
                item = _line_item_from_word_row(pending, inferred_currency)
                if item:
                    items.append(item)
                pending = None
            break

        if _is_table_stop_row(row):
            if pending:
                item = _line_item_from_word_row(pending, inferred_currency)
                if item:
                    items.append(item)
                pending = None
            break

        if _is_wrapped_description_row(row):
            if pending and gap <= 28:
                pending = _merge_word_table_rows(pending, row, header)
                previous_bottom = row.bbox[3]
            continue

        if not _line_item_from_word_row(row, inferred_currency):
            previous_bottom = row.bbox[3]
            continue

        if pending:
            item = _line_item_from_word_row(pending, inferred_currency)
            if item:
                items.append(item)
        pending = row
        previous_bottom = row.bbox[3]

    if pending:
        item = _line_item_from_word_row(pending, inferred_currency)
        if item:
            items.append(item)

    return items


def _infer_table_header(line: Line, lines: list[Line]) -> TableHeader | None:
    header_words, trim_x0 = _trim_header_words(line)
    if len(header_words) < 2:
        return None

    labels = _select_header_labels(header_words)
    resolved = _resolve_header_columns(labels)
    if not resolved:
        return None

    page_right = max((candidate.x1 for candidate in lines if candidate.page == line.page), default=line.x1)
    panel_x0 = trim_x0 or _nearby_total_panel_x0(line, lines, resolved[-1][0].x1)
    region_x0 = max(0.0, min(label.x0 for label, _role in resolved) - 8.0)
    region_x1 = (panel_x0 - 6.0) if panel_x0 else (page_right + 4.0)
    if region_x1 <= resolved[-1][0].x1 + 6:
        return None

    columns = _columns_with_ranges(resolved, region_x0=region_x0, region_x1=region_x1)
    if not any(column.role == "description" for column in columns):
        return None
    if not any(column.role == "amount" for column in columns):
        return None
    return TableHeader(
        page=line.page,
        line=line,
        columns=tuple(columns),
        region_x0=round(region_x0, 2),
        region_x1=round(region_x1, 2),
    )


def _trim_header_words(line: Line) -> tuple[tuple[Word, ...], float | None]:
    words = list(line.words)
    money_index = _first_money_like_word_index(words)
    if money_index == len(words):
        return tuple(words), None

    usable = words[:money_index]
    while usable and _is_currency_word(usable[-1].text):
        usable.pop()
    suffix_start = _total_label_suffix_start(tuple(usable))
    if suffix_start is None:
        return tuple(words), None
    trim_x0 = usable[suffix_start].x0
    return tuple(usable[:suffix_start]), trim_x0


def _select_header_labels(words: tuple[Word, ...]) -> tuple[HeaderLabel, ...]:
    tokens = [_header_word_token(word.text) for word in words]
    labels: list[HeaderLabel] = []
    index = 0
    while index < len(words):
        matches = [
            candidate
            for candidate in _column_label_candidates(words, tokens)
            if candidate.start == index
        ]
        if not matches:
            index += 1
            continue
        match = max(
            matches,
            key=lambda item: (
                item.end - item.start,
                _header_role_score(item.roles),
            ),
        )
        labels.append(match)
        index = match.end
    return tuple(labels)


def _column_label_candidates(words: tuple[Word, ...], tokens: list[str]) -> list[HeaderLabel]:
    grouped: dict[tuple[int, int, str], set[str]] = {}
    for role, labels in _table_role_labels().items():
        for label in labels:
            label_tokens = _table_label_tokens(label)
            if not label_tokens:
                continue
            width = len(label_tokens)
            for start in range(0, len(tokens) - width + 1):
                if tokens[start : start + width] == label_tokens:
                    grouped.setdefault((start, start + width, label), set()).add(role)

    candidates: list[HeaderLabel] = []
    for (start, end, label), roles in grouped.items():
        matched_words = words[start:end]
        candidates.append(
            HeaderLabel(
                label=label,
                roles=frozenset(roles),
                start=start,
                end=end,
                x0=min(word.x0 for word in matched_words),
                x1=max(word.x1 for word in matched_words),
            )
        )
    return candidates


def _table_role_labels() -> dict[str, tuple[str, ...]]:
    return {
        "description": TABLE_DESCRIPTION_LABELS,
        "quantity": TABLE_QUANTITY_LABELS,
        "unit_price": TABLE_UNIT_PRICE_LABELS,
        "amount": TABLE_AMOUNT_LABELS,
        "other": TABLE_OTHER_LABELS,
    }


def _resolve_header_columns(labels: tuple[HeaderLabel, ...]) -> list[tuple[HeaderLabel, str]]:
    if not labels:
        return []
    amount_indexes = [index for index, label in enumerate(labels) if "amount" in label.roles]
    if not amount_indexes:
        return []
    amount_index = max(amount_indexes)
    labels = labels[: amount_index + 1]

    unit_indexes = [
        index
        for index, label in enumerate(labels[:amount_index])
        if "unit_price" in label.roles
    ]
    unit_index = max(unit_indexes) if unit_indexes else None
    quantity_indexes = [
        index
        for index, label in enumerate(labels[:amount_index])
        if "quantity" in label.roles and index != unit_index
    ]
    description_indexes = [
        index
        for index, label in enumerate(labels[:amount_index])
        if "description" in label.roles
    ]
    if not description_indexes:
        return []

    numeric_indexes = quantity_indexes + ([unit_index] if unit_index is not None else []) + [amount_index]
    first_numeric_index = min(numeric_indexes)
    description_before_numeric = [index for index in description_indexes if index < first_numeric_index]
    strong_description_before_numeric = [
        index
        for index in description_before_numeric
        if labels[index].roles == frozenset({"description"})
    ]
    description_index = (
        strong_description_before_numeric[-1]
        if strong_description_before_numeric
        else description_before_numeric[-1]
        if description_before_numeric
        else description_indexes[-1]
    )

    resolved: list[tuple[HeaderLabel, str]] = []
    for index, label in enumerate(labels):
        if index == amount_index:
            role = "amount"
        elif index == unit_index:
            role = "unit_price"
        elif index in quantity_indexes:
            role = "quantity"
        elif index == description_index:
            role = "description"
        else:
            role = "other"
        resolved.append((label, role))
    return resolved


def _columns_with_ranges(
    resolved: list[tuple[HeaderLabel, str]],
    *,
    region_x0: float,
    region_x1: float,
) -> list[TableColumn]:
    columns: list[TableColumn] = []
    for index, (label, role) in enumerate(resolved):
        left = region_x0 if index == 0 else label.x0 - 2.0
        right = region_x1 if index == len(resolved) - 1 else resolved[index + 1][0].x0 - 2.0
        if right <= left:
            right = label.x1 + 4.0
        columns.append(
            TableColumn(
                role=role,
                label=label.label,
                x0=round(label.x0, 2),
                x1=round(label.x1, 2),
                range_x0=round(left, 2),
                range_x1=round(right, 2),
            )
        )
    return columns


def _nearby_total_panel_x0(line: Line, lines: list[Line], last_header_x1: float) -> float | None:
    candidates: list[float] = []
    minimum_x = max(last_header_x1 + 24.0, line.x0 + 80.0)
    for candidate in lines:
        if candidate.page != line.page:
            continue
        if candidate.top < line.top - 12 or candidate.top > line.top + 180:
            continue
        for start, end in _total_label_spans(candidate.words):
            matched_words = candidate.words[start:end]
            x0 = min(word.x0 for word in matched_words)
            if x0 > minimum_x:
                candidates.append(x0)
    return min(candidates) if candidates else None


def _table_rows_from_header(words: list[Word], header: TableHeader) -> list[WordTableRow]:
    candidates = [
        word
        for word in words
        if word.page == header.page
        and word.top > header.line.bottom + 0.5
        and _word_mid_x(word) >= header.region_x0 - 2.0
        and word.x0 < header.region_x1 + 2.0
    ]
    rows: list[WordTableRow] = []
    for group in _group_words_by_y(candidates):
        row_words = tuple(
            sorted(
                (
                    word
                    for word in group
                    if _word_mid_x(word) >= header.region_x0 - 2.0
                    and word.x0 < header.region_x1 + 2.0
                ),
                key=lambda item: (item.top, item.x0),
            )
        )
        if row_words:
            rows.append(_make_word_table_row(row_words, header))
    return rows


def _group_words_by_y(words: Iterable[Word]) -> list[tuple[Word, ...]]:
    groups: list[list[Word]] = []
    for word in sorted(words, key=lambda item: (item.page, item.top, item.x0)):
        if not groups:
            groups.append([word])
            continue
        current = groups[-1]
        if current[-1].page != word.page:
            groups.append([word])
            continue
        current_mid = sum((item.top + item.bottom) / 2 for item in current) / len(current)
        word_mid = (word.top + word.bottom) / 2
        tolerance = max(3.2, (word.bottom - word.top) * 0.58)
        if abs(word_mid - current_mid) <= tolerance:
            current.append(word)
        else:
            groups.append([word])
    return [tuple(sorted(group, key=lambda item: item.x0)) for group in groups]


def _make_word_table_row(words: tuple[Word, ...], header: TableHeader) -> WordTableRow:
    cells = _assign_table_cells(words, header)
    cells = _repair_description_overflow(cells)
    return WordTableRow(
        page=header.page,
        words=tuple(sorted(words, key=lambda item: (item.top, item.x0))),
        cells=cells,
        raw=_words_text(words),
        bbox=_bbox_for_words(words),
    )


def _merge_word_table_rows(
    row: WordTableRow,
    continuation: WordTableRow,
    header: TableHeader,
) -> WordTableRow:
    return _make_word_table_row((*row.words, *continuation.words), header)


def _assign_table_cells(words: tuple[Word, ...], header: TableHeader) -> dict[str, tuple[Word, ...]]:
    cells: dict[str, list[Word]] = {column.role: [] for column in header.columns}
    for word in words:
        column = _column_for_word(word, header.columns)
        if column is None:
            continue
        cells.setdefault(column.role, []).append(word)
    return {
        role: tuple(sorted(role_words, key=lambda item: (item.top, item.x0)))
        for role, role_words in cells.items()
        if role_words
    }


def _column_for_word(word: Word, columns: tuple[TableColumn, ...]) -> TableColumn | None:
    center = _word_mid_x(word)
    for column in columns:
        if column.range_x0 <= center < column.range_x1:
            return column
    if columns and center >= columns[-1].range_x1:
        return columns[-1]
    return None


def _repair_description_overflow(
    cells: dict[str, tuple[Word, ...]]
) -> dict[str, tuple[Word, ...]]:
    description_words = list(cells.get("description") or ())
    changed = False
    for role in ("quantity", "unit_price", "amount"):
        role_words = list(cells.get(role) or ())
        if not role_words:
            continue
        numeric_index = next(
            (
                index
                for index, word in enumerate(role_words)
                if _word_has_money(word)
                or _is_number_word(word.text)
                or _is_currency_word(word.text)
            ),
            None,
        )
        if numeric_index is None:
            description_words.extend(role_words)
            cells.pop(role, None)
            changed = True
            continue
        overflow = [
            word
            for word in role_words[:numeric_index]
            if not _word_has_money(word) and not _is_currency_word(word.text)
        ]
        if overflow:
            description_words.extend(overflow)
            remaining = [word for word in role_words if word not in overflow]
            cells[role] = tuple(remaining)
            changed = True
    if changed:
        cells["description"] = tuple(sorted(description_words, key=lambda item: (item.top, item.x0)))
    return cells


def _is_wrapped_description_row(row: WordTableRow) -> bool:
    if _parse_money_values(row.raw):
        return False
    if row.cells.get("quantity") or row.cells.get("unit_price") or row.cells.get("amount"):
        return False
    return bool(row.cells.get("description"))


def _line_item_from_word_row(
    row: WordTableRow,
    inferred_currency: str | None,
) -> dict[str, Any] | None:
    amount = _money_field_from_words(
        row.cells.get("amount") or (),
        inferred_currency,
        page=row.page,
        label="amount",
        confidence=0.88,
    )
    if amount is None:
        amount = _rightmost_money_field_from_row(row, inferred_currency)
    if amount is None:
        return None

    description_words = row.cells.get("description") or ()
    if not description_words:
        description_words = _description_words_before_amount(row)
    description = _clean_line_item_description(_words_text(description_words))
    if not description or _looks_like_non_item(description):
        return None

    quantity = _quantity_field_from_words(
        row.cells.get("quantity") or (),
        page=row.page,
        confidence=0.82,
    )
    if quantity is None:
        quantity = _quantity_before_amount(row)

    unit_price = _money_field_from_words(
        row.cells.get("unit_price") or (),
        inferred_currency,
        page=row.page,
        label="unit_price",
        confidence=0.8,
    )

    confidence = 0.84
    if quantity:
        confidence += 0.03
    if unit_price:
        confidence += 0.03
    quantity_value = quantity.get("value") if isinstance(quantity, dict) else None
    unit_price_value = unit_price.get("amount") if isinstance(unit_price, dict) else None
    return {
        "value": {
            "description": description,
            "quantity": quantity_value,
            "unit_price": unit_price_value,
            "amount": amount["amount"],
            "currency": amount.get("currency"),
        },
        "description": _field_value(
            raw=description,
            value=description,
            page=row.page,
            bbox=_bbox_for_words(description_words),
            label=None,
            confidence=0.86,
            method="word_table",
        ),
        "quantity": quantity,
        "unit_price": unit_price,
        "amount": amount,
        "raw": description,
        "row_raw": row.raw,
        "page": row.page,
        "bbox": row.bbox,
        "confidence": round(min(confidence, 0.93), 3),
        "method": "word_table",
    }


def _money_field_from_words(
    words: tuple[Word, ...],
    inferred_currency: str | None,
    *,
    page: int,
    label: str,
    confidence: float,
) -> dict[str, Any] | None:
    if not words:
        return None
    span = _rightmost_money_span(words)
    if span is None:
        return None
    return _money_field(
        money=span.money,
        currency=_currency_for_money(span.money, inferred_currency),
        page=page,
        bbox=_bbox_for_words(span.words),
        label=label,
        confidence=confidence,
        method="word_table",
    )


def _rightmost_money_field_from_row(
    row: WordTableRow,
    inferred_currency: str | None,
) -> dict[str, Any] | None:
    money_cells: list[MoneyWordSpan] = []
    for role_words in row.cells.values():
        if not role_words:
            continue
        span = _rightmost_money_span(role_words)
        if span is None:
            continue
        money_cells.append(span)
    if not money_cells:
        return None
    span = max(money_cells, key=lambda item: max(word.x1 for word in item.words))
    return _money_field(
        money=span.money,
        currency=_currency_for_money(span.money, inferred_currency),
        page=row.page,
        bbox=_bbox_for_words(span.words),
        label="amount",
        confidence=0.76,
        method="word_table",
    )


def _rightmost_money_span(words: tuple[Word, ...]) -> MoneyWordSpan | None:
    spans = _money_spans_in_words(words)
    if not spans:
        return None
    return max(
        spans,
        key=lambda item: (
            max(word.x1 for word in item.words),
            len(item.words),
        ),
    )


def _money_spans_in_words(words: tuple[Word, ...]) -> list[MoneyWordSpan]:
    ordered = tuple(sorted(words, key=lambda item: (item.top, item.x0)))
    spans: list[MoneyWordSpan] = []
    for start, word in enumerate(ordered):
        if not _is_money_part_word(word):
            continue
        group: list[Word] = []
        for current in ordered[start : min(len(ordered), start + 3)]:
            if group and current.x0 - group[-1].x1 > 20.0:
                break
            if not _is_money_part_word(current):
                break
            group.append(current)
            phrase = _words_text(group)
            money_values = _parse_money_values(phrase)
            if money_values:
                spans.append(MoneyWordSpan(words=tuple(group), money=money_values[-1]))
    return spans


def _is_money_part_word(word: Word) -> bool:
    return _word_has_money(word) or _is_currency_word(word.text) or _is_number_word(word.text)


def _currency_for_money(money: MoneyMention, inferred_currency: str | None) -> str | None:
    if money.currency:
        return money.currency
    if _has_ambiguous_yen_symbol(money.raw):
        return inferred_currency if inferred_currency in {"CNY", "JPY"} else None
    return inferred_currency


def _has_ambiguous_yen_symbol(raw: str) -> bool:
    return bool(re.search(r"(?:¥|\bY\b)", raw, re.IGNORECASE))


def _quantity_field_from_words(
    words: tuple[Word, ...],
    *,
    page: int,
    confidence: float,
) -> dict[str, Any] | None:
    if not words:
        return None
    for word in words:
        value = _parse_quantity_token(word.text)
        if value is None:
            continue
        return _field_value(
            raw=_words_text(words),
            value=value,
            page=page,
            bbox=_bbox_for_words(words),
            label="quantity",
            confidence=confidence,
            method="word_table",
        )
    return None


def _quantity_before_amount(row: WordTableRow) -> dict[str, Any] | None:
    amount_words = row.cells.get("amount") or ()
    if not amount_words:
        return None
    amount_left = min(word.x0 for word in amount_words)
    candidates = [
        word
        for word in row.words
        if word.x1 < amount_left
        and _parse_quantity_token(word.text) is not None
        and not _looks_like_year(word.text)
    ]
    if not candidates:
        return None
    word = candidates[-1]
    value = _parse_quantity_token(word.text)
    if value is None:
        return None
    return _field_value(
        raw=word.text,
        value=value,
        page=row.page,
        bbox=_bbox_for_words((word,)),
        label="quantity",
        confidence=0.62,
        method="word_table",
    )


def _parse_quantity_token(text: str) -> int | float | None:
    token = text.strip().replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d+)?", token):
        return None
    if _looks_like_year(token):
        return None
    try:
        value = float(token) if "." in token else int(token)
    except ValueError:
        return None
    if value <= 0 or value > 10000:
        return None
    return value


def _description_words_before_amount(row: WordTableRow) -> tuple[Word, ...]:
    amount_words = row.cells.get("amount") or ()
    if not amount_words:
        return ()
    amount_left = min(word.x0 for word in amount_words)
    return tuple(
        word
        for word in row.words
        if word.x1 < amount_left
        and not _word_has_money(word)
        and not _is_currency_word(word.text)
        and _parse_quantity_token(word.text) is None
    )


def _clean_line_item_description(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip(" :-")


def _header_role_score(roles: frozenset[str]) -> int:
    scores = {
        "amount": 5,
        "unit_price": 4,
        "description": 3,
        "quantity": 2,
        "other": 1,
    }
    return max((scores.get(role, 0) for role in roles), default=0)


def _table_label_tokens(label: str) -> list[str]:
    if label == "#":
        return ["#"]
    return [_header_word_token(token) for token in re.findall(r"[A-Za-z0-9$€£¥#/]+", label) if _header_word_token(token)]


def _header_word_token(value: str) -> str:
    if value.strip() == "#":
        return "#"
    return _canonical_token(value)


def _total_label_suffix_start(words: tuple[Word, ...]) -> int | None:
    tokens = [_header_word_token(word.text) for word in words]
    best_start: int | None = None
    for label in _table_stop_labels():
        label_tokens = _table_label_tokens(label)
        if not label_tokens or len(label_tokens) > len(tokens):
            continue
        start = len(tokens) - len(label_tokens)
        if tokens[start:] == label_tokens:
            if best_start is None or start < best_start:
                best_start = start
    return best_start


def _total_label_spans(words: tuple[Word, ...]) -> list[tuple[int, int]]:
    tokens = [_header_word_token(word.text) for word in words]
    spans: list[tuple[int, int]] = []
    for label in _table_stop_labels():
        label_tokens = _table_label_tokens(label)
        if not label_tokens:
            continue
        width = len(label_tokens)
        for start in range(0, len(tokens) - width + 1):
            if tokens[start : start + width] == label_tokens:
                spans.append((start, start + width))
    return spans


def _table_stop_labels() -> tuple[str, ...]:
    labels: list[str] = ["total"]
    for key in ("subtotal", "discount", "tax", "shipping", "paid", "balance_due"):
        labels.extend(LABELS[key])
    return tuple(labels)


def _is_table_stop_row(row: WordTableRow) -> bool:
    tokens = [_header_word_token(word.text) for word in row.words if _header_word_token(word.text)]
    if not tokens:
        return False

    for label in (*LABELS["payment_instructions"], *NON_ITEM_ROW_START_LABELS):
        label_tokens = _table_label_tokens(label)
        if label_tokens and tokens[: len(label_tokens)] == label_tokens:
            return True

    first_money_index = next(
        (
            index
            for index, word in enumerate(row.words)
            if _word_has_money(word) or _is_currency_word(word.text)
        ),
        len(row.words),
    )
    for label in _table_stop_labels():
        label_tokens = _table_label_tokens(label)
        if not label_tokens or tokens[: len(label_tokens)] != label_tokens:
            continue
        remaining = [
            _header_word_token(word.text)
            for word in row.words[len(label_tokens) : first_money_index]
            if _header_word_token(word.text)
            and not _is_currency_word(word.text)
        ]
        if len(remaining) <= 2:
            return True
    return False


def _word_has_money(word: Word) -> bool:
    return bool(_parse_money_values(word.text))


def _first_money_like_word_index(words: list[Word] | tuple[Word, ...]) -> int:
    for index, word in enumerate(words):
        if _word_has_money(word):
            return index
        if _is_currency_word(word.text) and any(_is_number_word(item.text) for item in words[index + 1 : index + 3]):
            return index
        if (
            _is_number_word(word.text)
            and index > 0
            and (_is_currency_word(words[index - 1].text) or _is_number_word(words[index - 1].text))
        ):
            return index - 1
    return len(words)


def _is_currency_word(text: str) -> bool:
    value = text.strip()
    if not value:
        return False
    upper = value.upper().rstrip(".")
    return (
        upper in ISO_CURRENCY_CODES
        or upper == "RMB"
        or value in SYMBOL_CURRENCY_MAP
        or value in {"¥", "Y", "Mex$", "US$", "S$", "C$", "A$", "Rs", "Rs."}
    )


def _is_number_word(text: str) -> bool:
    return bool(re.fullmatch(r"\(?[-+]?\d[\d,.'’]*(?:[.,]\d+)?\)?", text.strip()))


def _looks_like_year(text: str) -> bool:
    token = text.strip()
    return len(token) == 4 and token.startswith(("19", "20")) and token.isdigit()


def _word_mid_x(word: Word) -> float:
    return (word.x0 + word.x1) / 2


def _words_text(words: Iterable[Word]) -> str:
    return " ".join(word.text for word in sorted(words, key=lambda item: (item.top, item.x0))).strip()


def _match_any_label(line: Line, labels: Iterable[str]) -> LabelMatch | None:
    tokens = [_canonical_token(word.text) for word in line.words]
    best: LabelMatch | None = None
    for label in sorted(labels, key=lambda item: len(_label_tokens(item)), reverse=True):
        label_tokens = _label_tokens(label)
        if not label_tokens:
            continue
        for index in range(0, len(tokens) - len(label_tokens) + 1):
            if tokens[index : index + len(label_tokens)] == label_tokens:
                match = LabelMatch(label, index, index + len(label_tokens))
                if not best or len(_label_tokens(match.label)) > len(_label_tokens(best.label)):
                    best = match
    return best


def _value_after_label(
    lines: list[Line],
    line: Line,
    match: LabelMatch,
    *,
    allow_below: bool,
) -> tuple[str, list[float] | None, str]:
    words = tuple(word for word in line.words[match.end :] if _canonical_token(word.text))
    if words:
        return " ".join(word.text for word in words), _bbox_for_words(words), "same_line"
    if allow_below:
        for candidate in _following_lines(lines, line, max_gap=32, max_lines=2):
            if _matches_any_known_label(candidate):
                continue
            return candidate.text, _bbox(candidate), "below"
    return "", _bbox(line), "same_line"


def _following_lines(lines: list[Line], line: Line, *, max_gap: float, max_lines: int) -> list[Line]:
    found: list[Line] = []
    for candidate in lines:
        if candidate.page != line.page or candidate.top <= line.bottom:
            continue
        if candidate.top - line.bottom > max_gap:
            break
        found.append(candidate)
        if len(found) >= max_lines:
            break
    return found


def _line_from_words(page: int, words: tuple[Word, ...]) -> Line:
    return Line(
        page=page,
        words=words,
        text=" ".join(word.text for word in words),
        x0=min(word.x0 for word in words),
        top=min(word.top for word in words),
        x1=max(word.x1 for word in words),
        bottom=max(word.bottom for word in words),
    )


def _parse_money_values(text: str) -> list[MoneyMention]:
    values: list[MoneyMention] = []
    for match in MONEY_RE.finditer(text):
        raw = match.group(0).strip()
        number_raw = match.group("number")
        if not raw or not number_raw:
            continue
        prefix = match.group("prefix")
        suffix = match.group("suffix")
        if (
            suffix
            and not prefix
            and re.fullmatch(r"\d{1,2}", number_raw.strip())
            and re.match(r"\s+\d", text[match.end() :])
        ):
            continue
        has_currency = bool((prefix or "").strip() or (suffix or "").strip())
        if not has_currency and not _is_plausible_bare_money(number_raw):
            continue
        amount = _parse_amount(number_raw)
        if amount is None:
            continue
        currency = _normalize_currency(prefix or suffix)
        values.append(MoneyMention(raw=raw, amount=amount, currency=currency, span=match.span()))
    return values


def _parse_amount(raw: str) -> float | None:
    value = raw.strip()
    if not value:
        return None
    negative = value.startswith("(") and value.endswith(")")
    value = value.strip("()").replace("'", "").replace("’", "").replace(" ", "")
    value = value.replace("+", "")
    if value.startswith("-"):
        negative = True
        value = value[1:]
    if not value or not any(char.isdigit() for char in value):
        return None

    if "," in value and "." in value:
        decimal_separator = "," if value.rfind(",") > value.rfind(".") else "."
        thousands_separator = "." if decimal_separator == "," else ","
        value = value.replace(thousands_separator, "")
        value = value.replace(decimal_separator, ".")
    elif "," in value:
        parts = value.split(",")
        if len(parts[-1]) in {1, 2}:
            value = "".join(parts[:-1]).replace(",", "") + "." + parts[-1]
        else:
            value = value.replace(",", "")
    elif value.count(".") > 1:
        parts = value.split(".")
        if len(parts[-1]) in {1, 2}:
            value = "".join(parts[:-1]) + "." + parts[-1]
        else:
            value = "".join(parts)

    try:
        amount = float(Decimal(value))
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


def _is_plausible_bare_money(raw: str) -> bool:
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return False
    if len(digits) <= 1:
        return False
    if len(digits) == 8 and raw.isdigit():
        return False
    return True


def _normalize_currency(raw: str | None) -> str | None:
    if not raw:
        return None
    value = raw.strip()
    upper = value.upper().rstrip(".")
    if upper in ISO_CURRENCY_CODES:
        return upper
    if upper == "RMB":
        return "CNY"
    if value == "¥" or upper == "Y":
        return None
    for symbol, currency in SYMBOL_CURRENCY_MAP.items():
        if value == symbol:
            return currency
    if upper == "RS":
        return "INR"
    return None


def _parse_date(text: str) -> tuple[str, str] | None:
    clean = text.strip()
    if not clean:
        return None

    month_name_match = re.search(
        r"\b(?:(?P<d1>\d{1,2})\s+(?P<m1>[A-Za-z]{3,9})\s+(?P<y1>\d{2,4})|"
        r"(?P<m2>[A-Za-z]{3,9})\s+(?P<d2>\d{1,2}),?\s+(?P<y2>\d{2,4}))\b",
        clean,
    )
    if month_name_match:
        if month_name_match.group("m1"):
            day = int(month_name_match.group("d1"))
            month = MONTHS.get(month_name_match.group("m1").lower())
            year = _normalize_year(int(month_name_match.group("y1")))
        else:
            day = int(month_name_match.group("d2"))
            month = MONTHS.get(month_name_match.group("m2").lower())
            year = _normalize_year(int(month_name_match.group("y2")))
        parsed = _safe_date(year, month, day)
        if parsed:
            return month_name_match.group(0), parsed.isoformat()

    separated_match = re.search(r"\b(\d{1,4})[./-](\d{1,2})[./-](\d{1,4})\b", clean)
    if separated_match:
        first, second, third = [int(part) for part in separated_match.groups()]
        parsed = None
        if first > 1900:
            parsed = _safe_date(first, second, third)
        elif third > 1900:
            if first > 12:
                parsed = _safe_date(third, second, first)
            elif second > 12:
                parsed = _safe_date(third, first, second)
            else:
                parsed = _safe_date(third, first, second)
        elif third < 100:
            year = _normalize_year(third)
            if first > 12:
                parsed = _safe_date(year, second, first)
            elif second > 12:
                parsed = _safe_date(year, first, second)
            else:
                parsed = _safe_date(year, first, second)
        if parsed:
            return separated_match.group(0), parsed.isoformat()

    compact_match = re.search(r"\b(\d{8}|\d{6})\b", clean)
    if compact_match:
        raw = compact_match.group(1)
        parsed = None
        if len(raw) == 8 and raw.startswith(("19", "20")):
            parsed = _safe_date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        elif len(raw) == 8:
            first = int(raw[:2])
            second = int(raw[2:4])
            year = int(raw[4:8])
            if first > 12:
                parsed = _safe_date(year, second, first)
            elif second > 12:
                parsed = _safe_date(year, first, second)
            else:
                parsed = _safe_date(year, first, second)
        elif len(raw) == 6:
            first = int(raw[:2])
            second = int(raw[2:4])
            year = _normalize_year(int(raw[4:6]))
            if first > 12:
                parsed = _safe_date(year, second, first)
            elif second > 12:
                parsed = _safe_date(year, first, second)
            else:
                parsed = _safe_date(year, first, second)
        if parsed:
            return raw, parsed.isoformat()

    return None


def _normalize_year(year: int) -> int:
    if year < 100:
        return 2000 + year if year < 70 else 1900 + year
    return year


def _safe_date(year: int | None, month: int | None, day: int | None) -> date | None:
    if year is None or month is None or day is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _field_value(
    *,
    raw: Any,
    value: Any,
    page: int,
    bbox: list[float] | None,
    label: str | None,
    confidence: float,
    method: str,
) -> dict[str, Any]:
    return {
        "raw": raw,
        "value": value,
        "page": page,
        "bbox": bbox,
        "label": label,
        "confidence": round(confidence, 3),
        "method": method,
    }


def _money_field(
    *,
    money: MoneyMention,
    currency: str | None,
    page: int,
    bbox: list[float] | None,
    label: str | None,
    confidence: float,
    method: str,
) -> dict[str, Any]:
    return {
        "raw": money.raw,
        "value": round(money.amount, 2),
        "amount": round(money.amount, 2),
        "currency": currency,
        "page": page,
        "bbox": bbox,
        "label": label,
        "confidence": round(confidence, 3),
        "method": method,
    }


def _best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            float(item.get("confidence") or 0),
            float((item.get("bbox") or [0, 0, 0, 0])[3]),
        ),
    )


def _normalize_scalar_value(key: str, raw: str) -> str | None:
    value = raw.strip(" :#\t\r\n")
    if not value:
        return None
    if key == "invoice_number":
        value = value.split()[0] if len(value) > 80 else value
    if key == "purchase_order":
        value = value.split()[0] if len(value) > 80 else value
    return value[:300]


def _clean_field_raw(raw: str) -> str:
    return re.sub(r"\s+", " ", raw.replace("\u00a0", " ")).strip(" :")


def _trim_at_known_label(raw: str) -> str:
    value = raw.strip()
    lower = " " + _canonical_label(value)
    cut_positions: list[int] = []
    for labels in LABELS.values():
        for label in labels:
            canonical = _canonical_label(label)
            if not canonical:
                continue
            position = lower.find(" " + canonical, 1)
            if position > 0:
                cut_positions.append(position - 1)
    if cut_positions:
        value = value[: min(cut_positions)].strip(" :")
    return value


def _looks_like_invoice_number(value: str) -> bool:
    return bool(re.search(r"[A-Z]{1,}|[#/-]|\d{3,}", value, re.IGNORECASE))


def _description_before_amount(text: str, amount_raw: str) -> str:
    index = text.rfind(amount_raw)
    description = text[:index] if index >= 0 else text
    description = re.sub(r"\s+", " ", description).strip(" :-")
    for money in _parse_money_values(description):
        description = description.replace(money.raw, " ")
    return re.sub(r"\s+", " ", description).strip(" :-")


def _looks_like_non_item(description: str) -> bool:
    canonical = _canonical_label(description)
    summary_labels = LABELS["subtotal"] + LABELS["discount"] + LABELS["tax"] + LABELS["paid"] + LABELS["balance_due"]
    if any(
        (label_canonical := _canonical_label(label))
        and len(label_canonical) >= 4
        and canonical.startswith(label_canonical)
        for label in summary_labels
    ):
        return True
    return any(_canonical_label(label) and canonical.startswith(_canonical_label(label)) for label in NON_ITEM_ROW_START_LABELS)


def _matches_any_known_label(line: Line) -> bool:
    return any(_match_any_label(line, labels) for labels in LABELS.values())


def _matches_any_total_label(line: Line) -> bool:
    total_keys = ("subtotal", "discount", "tax", "shipping", "paid", "balance_due")
    return any(_match_any_label(line, LABELS[key]) for key in total_keys) or bool(_generic_total_match(line))


def _matches_total_text(text: str) -> bool:
    canonical = _canonical_label(text)
    return any(
        _canonical_label(label) in canonical
        for key in ("subtotal", "discount", "tax", "shipping", "paid", "balance_due")
        for label in LABELS[key]
    ) or "total" in canonical


def _is_table_header(line: Line) -> bool:
    return _is_table_header_text(line.text)


def _is_table_header_text(text: str) -> bool:
    canonical = _canonical_label(text)
    has_description = any(_canonical_label(label) in canonical for label in TABLE_DESCRIPTION_LABELS)
    has_amount = any(_canonical_label(label) in canonical for label in TABLE_AMOUNT_LABELS)
    return has_description and has_amount


def _is_bottom_or_right(line: Line) -> bool:
    return line.top > 360 or line.x0 > 260


def _horizontally_overlaps(a: Line, b: Line) -> bool:
    return min(a.x1, b.x1) - max(a.x0, b.x0) > 0


def _canonical_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _canonical_label(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _label_tokens(label: str) -> list[str]:
    return [token for token in (_canonical_token(part) for part in re.findall(r"[A-Za-z0-9$€£¥]+", label)) if token]


def _bbox(line: Line) -> list[float]:
    return [round(line.x0, 2), round(line.top, 2), round(line.x1, 2), round(line.bottom, 2)]


def _bbox_for_words(words: Iterable[Word]) -> list[float]:
    values = tuple(words)
    return [
        round(min(word.x0 for word in values), 2),
        round(min(word.top for word in values), 2),
        round(max(word.x1 for word in values), 2),
        round(max(word.bottom for word in values), 2),
    ]


def _merge_bboxes(lines: Iterable[Line]) -> list[float]:
    values = tuple(lines)
    return [
        round(min(line.x0 for line in values), 2),
        round(min(line.top for line in values), 2),
        round(max(line.x1 for line in values), 2),
        round(max(line.bottom for line in values), 2),
    ]


def _add_missing_warnings(fields: dict[str, Any], warnings: list[str]) -> None:
    for key in ("invoice_number", "issue_date", "due_date", "balance_due"):
        if not fields.get(key):
            warnings.append(f"Could not confidently extract {key}.")
    if not (fields.get("seller") or fields.get("buyer") or fields.get("line_items")):
        warnings.append("Could not confidently extract any invoice party or line item.")


def _add_line_item_total_warnings(fields: dict[str, Any], warnings: list[str]) -> None:
    line_items = fields.get("line_items")
    if not isinstance(line_items, list) or not line_items:
        return
    item_sum = 0.0
    counted = 0
    for item in line_items:
        if not isinstance(item, dict):
            continue
        amount = item.get("amount")
        if not isinstance(amount, dict):
            continue
        try:
            item_sum += float(amount["amount"])
        except (KeyError, TypeError, ValueError):
            continue
        counted += 1
    if counted == 0:
        return

    targets: list[tuple[str, float]] = []
    subtotal = fields.get("subtotal")
    if isinstance(subtotal, dict) and subtotal.get("amount") is not None:
        targets.append(("subtotal", float(subtotal["amount"])))
    elif _balance_is_reasonable_comparison_target(fields):
        balance = fields.get("balance_due")
        if isinstance(balance, dict) and balance.get("amount") is not None:
            targets.append(("balance_due", float(balance["amount"])))

    for label, expected in targets:
        tolerance = max(1.0, abs(expected) * 0.015)
        difference = round(item_sum - expected, 2)
        if abs(difference) > tolerance:
            warnings.append(
                f"Line item amount sum {item_sum:.2f} does not match {label} {expected:.2f}."
            )


def _balance_is_reasonable_comparison_target(fields: dict[str, Any]) -> bool:
    for key in ("discount", "tax", "shipping", "paid"):
        field = fields.get(key)
        if not isinstance(field, dict) or field.get("amount") is None:
            return False
        try:
            if abs(float(field["amount"])) > 0.01:
                return False
        except (TypeError, ValueError):
            return False
    return True
