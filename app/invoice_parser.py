from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from .invoice_ocr import (
    DocumentOcrEngine,
    DocumentOcrResult,
    OCR_CONFIDENCE_THRESHOLD,
    OCR_MAX_DOCUMENT_PAGES,
    OCR_MAX_REGIONS,
    OCR_REFINEMENT_DPI,
    OCR_RENDER_DPI,
    OCR_REGION_PADDING,
    OCR_TIMEOUT_SECONDS,
    OcrRegionCandidate,
    RegionOcrEngine,
    RegionOcrUnavailable,
    RegionOcrText,
    TesseractRegionOcrEngine,
    apply_low_confidence_region_ocr,
)
from .invoice_pipeline import (
    PageProfile,
    blocking_validation_failures,
    pipeline_metadata,
    profile_document_pages,
    validate_invoice_fields,
)

PARSER_VERSION = "static-pdf-v4"
OCR_AMBIGUOUS_MIN_CONFIDENCE = 0.70
PARSER_REVIEW_CONFIDENCE_THRESHOLD = 0.80
REQUIRED_NORMALIZED_FIELDS = (
    "invoice_number",
    "issue_date",
    "due_date",
    "currency",
    "seller",
    "buyer",
    "balance_due",
)

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
OCR_FALLBACK_FIELDS = (*FIELD_KEYS, "line_items")

LABELS: dict[str, tuple[str, ...]] = {
    "invoice_number": (
        "credit memo number",
        "credit memo no",
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
        "related po",
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
        "credit subtotal",
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
        "credit balance",
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
    confidence: float | None = None


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


def parse_invoice_pdf(
    content: bytes,
    *,
    source_id: str | None = None,
    enable_ocr: bool = True,
    enable_full_ocr_fallback: bool = True,
    ocr_confidence_threshold: float = OCR_CONFIDENCE_THRESHOLD,
    ocr_padding: float = OCR_REGION_PADDING,
    ocr_max_regions: int | None = OCR_MAX_REGIONS,
    ocr_max_document_pages: int | None = OCR_MAX_DOCUMENT_PAGES,
    ocr_render_dpi: int = OCR_RENDER_DPI,
    ocr_refinement_dpi: int = OCR_REFINEMENT_DPI,
    ocr_timeout_seconds: float | None = OCR_TIMEOUT_SECONDS,
    document_timeout_seconds: float | None = 90.0,
    ocr_engine: RegionOcrEngine | None = None,
    document_ocr_engine: DocumentOcrEngine | None = None,
) -> dict[str, Any]:
    pipeline_started_at = time.perf_counter()
    warnings: list[str] = []
    default_ocr_engine: TesseractRegionOcrEngine | None = None
    if enable_ocr and ocr_engine is None and document_ocr_engine is None:
        default_ocr_engine = TesseractRegionOcrEngine(
            dpi=ocr_render_dpi,
            refinement_dpi=ocr_refinement_dpi,
            timeout_seconds=ocr_timeout_seconds,
            document_timeout_seconds=document_timeout_seconds,
        )
        ocr_engine = default_ocr_engine
        document_ocr_engine = default_ocr_engine
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
    page_char_counts: dict[int, int] = {}

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
                page_char_counts[page_index] = len(page_chars)
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

    profiles = profile_document_pages(
        pages,
        words,
        page_char_counts=page_char_counts,
    )
    native_extraction_ms = (time.perf_counter() - pipeline_started_at) * 1000

    if _has_no_text_layer(char_count, words):
        if enable_ocr and enable_full_ocr_fallback:
            full_ocr = _run_full_document_ocr(
                content,
                warnings=warnings,
                ocr_engine=ocr_engine,
                document_ocr_engine=document_ocr_engine,
                page_numbers=_important_ocr_pages(
                    pages,
                    target_fields=OCR_FALLBACK_FIELDS,
                    max_pages=ocr_max_document_pages,
                ),
                max_pages=ocr_max_document_pages,
            )
            if full_ocr is not None:
                ocr_result, full_ocr_summary = full_ocr
                pages = _ocr_pages_to_parser_pages(ocr_result)
                words = _ocr_words_to_parser_words(ocr_result)
                full_ocr_summary["trigger"] = "no_text_layer"
                full_ocr_summary["missing_fields_before"] = list(REQUIRED_NORMALIZED_FIELDS)
                fields = _extract_fields_from_words(words, warnings)
                _calibrate_ocr_field_confidences(fields, words)
                _tag_full_document_ocr_fields(fields)
                targeted_fields = _adaptive_ocr_escalation_fields(fields, pages, profiles)
                if ocr_engine is None:
                    region_summary = _skipped_region_ocr_summary(
                        targeted_fields=targeted_fields,
                        max_regions=ocr_max_regions,
                        reason="region_ocr_engine_unavailable",
                    )
                elif _pipeline_budget_exhausted(pipeline_started_at, document_timeout_seconds):
                    region_summary = _skipped_region_ocr_summary(
                        targeted_fields=targeted_fields,
                        max_regions=ocr_max_regions,
                        reason="document_time_budget_exhausted",
                    )
                else:
                    region_summary = apply_low_confidence_region_ocr(
                        content,
                        fields=fields,
                        pages=pages,
                        warnings=warnings,
                        threshold=ocr_confidence_threshold,
                        padding=ocr_padding,
                        max_regions=ocr_max_regions,
                        target_fields=targeted_fields,
                        only_target_fields=True,
                        engine=ocr_engine,
                        field_updater=lambda field, candidate, ocr_text: _replace_field_with_ocr_result(
                            fields,
                            field,
                            candidate,
                            ocr_text,
                        ),
                    )
                region_summary["full_document"] = full_ocr_summary
                return _finalize_parse_result(
                    fields=fields,
                    pages=pages,
                    warnings=warnings,
                    source_id=source_id,
                    ocr_summary=region_summary,
                    page_profiles=profiles,
                    pipeline_started_at=pipeline_started_at,
                    native_extraction_ms=native_extraction_ms,
                    pipeline_configuration=_pipeline_configuration(
                        ocr_confidence_threshold=ocr_confidence_threshold,
                        ocr_padding=ocr_padding,
                        ocr_max_regions=ocr_max_regions,
                        ocr_max_document_pages=ocr_max_document_pages,
                        ocr_render_dpi=ocr_render_dpi,
                        ocr_refinement_dpi=ocr_refinement_dpi,
                        ocr_timeout_seconds=ocr_timeout_seconds,
                        document_timeout_seconds=document_timeout_seconds,
                    ),
                )
        return _empty_result(
            "needs_review" if enable_ocr and enable_full_ocr_fallback else "no_text_layer",
            pages=pages,
            warnings=[
                (
                    "PDF has no usable text layer; full-document OCR could not extract usable text."
                    if enable_ocr and enable_full_ocr_fallback
                    else "PDF has no usable text layer; OCR is disabled."
                ),
                *warnings,
            ],
            source_id=source_id,
        )

    fields = _extract_fields_from_words(words, warnings)
    ocr_summary = None
    if enable_ocr:
        escalation_fields = (
            _adaptive_ocr_escalation_fields(fields, pages, profiles)
            if default_ocr_engine is not None
            else _ocr_escalation_fields(fields, pages)
        )
        full_ocr_summary = None
        if (
            default_ocr_engine is not None
            and escalation_fields
            and enable_full_ocr_fallback
            and not _pipeline_budget_exhausted(pipeline_started_at, document_timeout_seconds)
        ):
            full_ocr_summary = _merge_full_document_ocr(
                content,
                fields=fields,
                pages=pages,
                warnings=warnings,
                ocr_engine=ocr_engine,
                document_ocr_engine=document_ocr_engine,
                escalation_fields=escalation_fields,
                ocr_max_document_pages=ocr_max_document_pages,
            )

        targeted_fields = (
            _adaptive_ocr_escalation_fields(fields, pages, profiles)
            if default_ocr_engine is not None
            else _ocr_escalation_fields(fields, pages)
        )
        if _pipeline_budget_exhausted(pipeline_started_at, document_timeout_seconds):
            _append_warning_once(warnings, "Local OCR document time budget was exhausted.")
            ocr_summary = _skipped_region_ocr_summary(
                targeted_fields=targeted_fields,
                max_regions=ocr_max_regions,
                reason="document_time_budget_exhausted",
            )
        else:
            ocr_summary = apply_low_confidence_region_ocr(
            content,
            fields=fields,
            pages=pages,
            warnings=warnings,
            threshold=ocr_confidence_threshold,
            padding=ocr_padding,
            max_regions=ocr_max_regions,
            target_fields=targeted_fields,
            only_target_fields=default_ocr_engine is not None,
            engine=ocr_engine,
            field_updater=lambda field, candidate, ocr_text: _replace_field_with_ocr_result(
                fields,
                field,
                candidate,
                ocr_text,
            ),
            )

        if default_ocr_engine is None:
            escalation_fields = _ocr_escalation_fields(fields, pages)
            if escalation_fields and enable_full_ocr_fallback:
                full_ocr_summary = _merge_full_document_ocr(
                    content,
                    fields=fields,
                    pages=pages,
                    warnings=warnings,
                    ocr_engine=ocr_engine,
                    document_ocr_engine=document_ocr_engine,
                    escalation_fields=escalation_fields,
                    ocr_max_document_pages=ocr_max_document_pages,
                )
        if full_ocr_summary is not None:
            ocr_summary["full_document"] = full_ocr_summary

    return _finalize_parse_result(
        fields=fields,
        pages=pages,
        warnings=warnings,
        source_id=source_id,
        ocr_summary=ocr_summary,
        page_profiles=profiles,
        pipeline_started_at=pipeline_started_at,
        native_extraction_ms=native_extraction_ms,
        pipeline_configuration=_pipeline_configuration(
            ocr_confidence_threshold=ocr_confidence_threshold,
            ocr_padding=ocr_padding,
            ocr_max_regions=ocr_max_regions,
            ocr_max_document_pages=ocr_max_document_pages,
            ocr_render_dpi=ocr_render_dpi,
            ocr_refinement_dpi=ocr_refinement_dpi,
            ocr_timeout_seconds=ocr_timeout_seconds,
            document_timeout_seconds=document_timeout_seconds,
        ),
    )


def _parse_words_result(
    *,
    words: list[Word],
    pages: list[dict[str, Any]],
    warnings: list[str],
    source_id: str | None,
    ocr_summary: dict[str, Any] | None = None,
    tag_fields_as_full_ocr: bool = False,
    page_profiles: list[PageProfile] | None = None,
    pipeline_started_at: float | None = None,
    native_extraction_ms: float = 0.0,
    pipeline_configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields = _extract_fields_from_words(words, warnings)
    if tag_fields_as_full_ocr:
        _calibrate_ocr_field_confidences(fields, words)
        _tag_full_document_ocr_fields(fields)
    return _finalize_parse_result(
        fields=fields,
        pages=pages,
        warnings=warnings,
        source_id=source_id,
        ocr_summary=ocr_summary,
        page_profiles=page_profiles,
        pipeline_started_at=pipeline_started_at,
        native_extraction_ms=native_extraction_ms,
        pipeline_configuration=pipeline_configuration,
    )


def _finalize_parse_result(
    *,
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
    warnings: list[str],
    source_id: str | None,
    ocr_summary: dict[str, Any] | None = None,
    page_profiles: list[PageProfile] | None = None,
    pipeline_started_at: float | None = None,
    native_extraction_ms: float = 0.0,
    pipeline_configuration: dict[str, Any] | None = None,
) -> dict[str, Any]:
    missing_required_fields = _missing_required_normalized_fields(fields)
    field_issues = _field_review_issues(fields)
    validations = validate_invoice_fields(fields)
    validation_failures = blocking_validation_failures(validations)
    _add_missing_warnings(fields, warnings)
    for failure in validation_failures:
        _append_warning_once(warnings, f"Invoice validation failed: {failure}.")
    needs_review = bool(missing_required_fields or field_issues or validation_failures)
    tracking = _ocr_tracking_metadata(
        fields=fields,
        ocr_summary=ocr_summary,
        missing_required_fields=missing_required_fields,
    )
    total_ms = (
        (time.perf_counter() - pipeline_started_at) * 1000
        if pipeline_started_at is not None
        else 0.0
    )
    ocr_diagnostics = _ocr_diagnostics(ocr_summary)
    result = {
        "status": "needs_review" if needs_review else "parsed",
        "parser_version": PARSER_VERSION,
        "fields": fields,
        "pages": pages,
        "warnings": warnings,
        **tracking,
        "pipeline": pipeline_metadata(
            profiles=page_profiles or [],
            ocr_used=bool(tracking["ocr_used"]),
            validations=validations,
            timings_ms={
                "native_extraction": native_extraction_ms,
                "ocr_and_resolution": max(0.0, total_ms - native_extraction_ms),
                "total": total_ms,
            },
            ocr_diagnostics=ocr_diagnostics,
            configuration=pipeline_configuration,
        ),
    }
    if needs_review:
        result["review"] = {
            "required": True,
            "reason": (
                "missing_required_normalized_data"
                if missing_required_fields
                else "low_confidence_or_ambiguous_fields"
                if field_issues
                else "failed_invoice_validation"
            ),
            "missing_fields": missing_required_fields,
            "field_issues": field_issues,
            "validation_failures": validation_failures,
        }
    if ocr_summary is not None:
        result["ocr"] = ocr_summary
    if source_id:
        result["source_id"] = source_id
    return result


def _field_review_issues(fields: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for key in REQUIRED_NORMALIZED_FIELDS:
        field = fields.get(key)
        if not isinstance(field, dict):
            continue
        _append_field_review_issue(issues, key, field)
    for index, item in enumerate(fields.get("line_items") or []):
        if not isinstance(item, dict):
            continue
        for role in ("description", "quantity", "amount"):
            field = item.get(role)
            if not isinstance(field, dict) or "ocr" not in str(field.get("method") or ""):
                continue
            _append_field_review_issue(issues, f"line_items[{index}].{role}", field)
    return issues


def _append_field_review_issue(
    issues: list[dict[str, Any]],
    path: str,
    field: dict[str, Any],
) -> None:
    confidence = _normalized_confidence(field.get("confidence"))
    reasons = _field_ambiguity_reasons(field)
    material_reasons = [reason for reason in reasons if reason != "weak_geometry"]
    method = str(field.get("method") or "")
    threshold = (
        OCR_CONFIDENCE_THRESHOLD
        if "ocr" in method
        else PARSER_REVIEW_CONFIDENCE_THRESHOLD
    )
    if (
        confidence is not None
        and confidence >= threshold
        and not material_reasons
    ):
        return
    issues.append(
        {
            "field": path,
            "confidence": round(confidence, 3) if confidence is not None else None,
            "reasons": material_reasons
            or ["missing_confidence" if confidence is None else "low_confidence"],
        }
    )


def _ocr_tracking_metadata(
    *,
    fields: dict[str, Any],
    ocr_summary: dict[str, Any] | None,
    missing_required_fields: list[str],
) -> dict[str, Any]:
    ocr_used = _ocr_summary_indicates_use(ocr_summary)
    return {
        "ocr_used": ocr_used,
        "ocr_parts": _ocr_parts(fields=fields, ocr_summary=ocr_summary),
        "normal_model_failed_parts": _normal_model_failed_parts(
            ocr_summary=ocr_summary,
            missing_required_fields=missing_required_fields,
        ),
        "ocr_failed_parts": missing_required_fields if ocr_used else [],
    }


def _ocr_diagnostics(ocr_summary: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(ocr_summary, dict):
        return {}
    full_document = ocr_summary.get("full_document")
    if not isinstance(full_document, dict):
        return {}
    diagnostics = full_document.get("diagnostics")
    return diagnostics if isinstance(diagnostics, dict) else {}


def _pipeline_configuration(
    *,
    ocr_confidence_threshold: float,
    ocr_padding: float,
    ocr_max_regions: int | None,
    ocr_max_document_pages: int | None,
    ocr_render_dpi: int,
    ocr_refinement_dpi: int,
    ocr_timeout_seconds: float | None,
    document_timeout_seconds: float | None,
) -> dict[str, Any]:
    return {
        "ocr_confidence_threshold": round(ocr_confidence_threshold, 3),
        "ocr_padding": round(ocr_padding, 3),
        "ocr_max_regions": ocr_max_regions,
        "ocr_max_document_pages": ocr_max_document_pages,
        "ocr_render_dpi": ocr_render_dpi,
        "ocr_refinement_dpi": ocr_refinement_dpi,
        "ocr_timeout_seconds": ocr_timeout_seconds,
        "document_timeout_seconds": document_timeout_seconds,
    }


def _pipeline_budget_exhausted(started_at: float, timeout_seconds: float | None) -> bool:
    return timeout_seconds is not None and time.perf_counter() - started_at >= timeout_seconds


def _skipped_region_ocr_summary(
    *,
    targeted_fields: list[str],
    max_regions: int | None,
    reason: str,
) -> dict[str, Any]:
    return {
        "status": "skipped",
        "reason": reason,
        "max_regions": max_regions,
        "candidate_count": len(targeted_fields),
        "attempted_count": 0,
        "applied_count": 0,
        "skipped_count": len(targeted_fields),
        "failed_count": 0,
        "capped_region_count": 0,
        "regions": [],
    }


def _ocr_summary_indicates_use(ocr_summary: dict[str, Any] | None) -> bool:
    if not isinstance(ocr_summary, dict):
        return False
    if isinstance(ocr_summary.get("full_document"), dict):
        return True
    try:
        return int(ocr_summary.get("attempted_count") or 0) > 0
    except (TypeError, ValueError):
        return False


def _ocr_parts(*, fields: dict[str, Any], ocr_summary: dict[str, Any] | None) -> list[str]:
    parts = set(_full_document_ocr_parts_from_fields(fields))
    if isinstance(ocr_summary, dict):
        parts.update(_applied_region_ocr_parts(ocr_summary))
        full_document = ocr_summary.get("full_document")
        if isinstance(full_document, dict):
            applied_fields = full_document.get("applied_fields")
            if isinstance(applied_fields, list):
                parts.update(str(part) for part in applied_fields if part)
    return sorted(parts)


def _normal_model_failed_parts(
    *,
    ocr_summary: dict[str, Any] | None,
    missing_required_fields: list[str],
) -> list[str]:
    parts: set[str] = set()
    if isinstance(ocr_summary, dict):
        full_document = ocr_summary.get("full_document")
        if isinstance(full_document, dict):
            before = full_document.get("missing_fields_before")
            if isinstance(before, list):
                parts.update(str(part) for part in before if part)
    if not parts:
        parts.update(missing_required_fields)
    return sorted(parts)


def _applied_region_ocr_parts(ocr_summary: dict[str, Any]) -> set[str]:
    parts: set[str] = set()
    regions = ocr_summary.get("regions")
    if not isinstance(regions, list):
        return parts
    for region in regions:
        if not isinstance(region, dict) or region.get("applied") is not True:
            continue
        path = region.get("path")
        if isinstance(path, str):
            part = _ocr_part_from_summary_path(path)
            if part:
                parts.add(part)
    return parts


def _full_document_ocr_parts_from_fields(value: Any, path: tuple[str | int, ...] = ()) -> set[str]:
    parts: set[str] = set()
    if isinstance(value, dict):
        method = value.get("method")
        if isinstance(method, str) and method.startswith("full_document_ocr:"):
            part = _ocr_part_from_path(path)
            if part:
                parts.add(part)
        for child_key, child_value in value.items():
            parts.update(_full_document_ocr_parts_from_fields(child_value, (*path, str(child_key))))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            parts.update(_full_document_ocr_parts_from_fields(item, (*path, index)))
    return parts


def _ocr_part_from_summary_path(path: str) -> str | None:
    if path.startswith("fields."):
        path = path[len("fields.") :]
    path = re.sub(r"\[\d+\]", "", path)
    return _ocr_part_from_path(tuple(part for part in path.split(".") if part))


def _ocr_part_from_path(path: tuple[str | int, ...]) -> str | None:
    if not path:
        return None
    if path[0] != "line_items":
        return str(path[0])
    for part in reversed(path):
        if isinstance(part, str) and part != "line_items":
            return f"line_items.{part}"
    return "line_items"


def _extract_fields_from_words(words: list[Word], warnings: list[str]) -> dict[str, Any]:
    lines = _build_lines(words)
    return _extract_fields(lines=lines, words=words, warnings=warnings)


def _merge_full_document_ocr(
    content: bytes,
    *,
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
    warnings: list[str],
    ocr_engine: RegionOcrEngine | None,
    document_ocr_engine: DocumentOcrEngine | None,
    escalation_fields: list[str],
    ocr_max_document_pages: int | None,
) -> dict[str, Any] | None:
    missing_before = _missing_required_normalized_fields(fields)
    selected_pages = _important_ocr_pages(
        pages,
        target_fields=escalation_fields,
        max_pages=ocr_max_document_pages,
    )
    full_ocr = _run_full_document_ocr(
        content,
        warnings=warnings,
        ocr_engine=ocr_engine,
        document_ocr_engine=document_ocr_engine,
        page_numbers=selected_pages,
        max_pages=ocr_max_document_pages,
    )
    if full_ocr is None:
        return None

    ocr_result, summary = full_ocr
    summary["trigger"] = "missing_required_fields" if missing_before else "unresolved_fields"
    summary["missing_fields_before"] = missing_before
    summary["target_fields"] = escalation_fields
    full_ocr_warnings: list[str] = []
    ocr_words = _ocr_words_to_parser_words(ocr_result)
    full_ocr_fields = _extract_fields_from_words(ocr_words, full_ocr_warnings)
    _calibrate_ocr_field_confidences(full_ocr_fields, ocr_words)
    _tag_full_document_ocr_fields(full_ocr_fields)
    summary["applied_fields"] = _merge_ocr_fields(
        fields,
        full_ocr_fields,
        target_fields=escalation_fields,
    )
    summary["missing_fields_after"] = _missing_required_normalized_fields(fields)
    for warning in full_ocr_warnings:
        _append_warning_once(warnings, f"Full-document OCR parse: {warning}")
    return summary


def _run_full_document_ocr(
    content: bytes,
    *,
    warnings: list[str],
    ocr_engine: RegionOcrEngine | None,
    document_ocr_engine: DocumentOcrEngine | None,
    page_numbers: list[int],
    max_pages: int | None,
) -> tuple[DocumentOcrResult, dict[str, Any]] | None:
    engine = _document_ocr_engine(ocr_engine=ocr_engine, document_ocr_engine=document_ocr_engine)
    try:
        try:
            ocr_result = engine.ocr_document(
                content,
                pages=page_numbers,
                max_pages=max_pages,
            )
        except TypeError as exc:
            if "unexpected keyword argument" not in str(exc):
                raise
            ocr_result = _filter_document_ocr_result(
                engine.ocr_document(content),
                page_numbers=page_numbers,
            )
    except RegionOcrUnavailable as exc:
        _append_warning_once(warnings, f"Full-document OCR unavailable: {exc}")
        return None
    except Exception as exc:
        _append_warning_once(warnings, f"Full-document OCR failed: {exc}")
        return None

    summary = {
        "status": "completed" if ocr_result.words else "skipped",
        "method": ocr_result.method,
        "page_count": len(ocr_result.pages),
        "selected_pages": [page.page for page in ocr_result.pages],
        "max_pages": max_pages,
        "word_count": len(ocr_result.words),
        "confidence": round(ocr_result.confidence, 3) if ocr_result.confidence is not None else None,
        "diagnostics": ocr_result.diagnostics,
    }
    if not ocr_result.words:
        summary["reason"] = "no_ocr_words"
        _append_warning_once(warnings, "Full-document OCR did not extract any words.")
    if ocr_result.diagnostics.get("document_budget_exhausted") is True:
        summary["status"] = "partial"
        summary["reason"] = "document_time_budget_exhausted"
        _append_warning_once(warnings, "Full-document OCR stopped at the document time budget.")
    return ocr_result, summary


def _filter_document_ocr_result(
    result: DocumentOcrResult,
    *,
    page_numbers: list[int],
) -> DocumentOcrResult:
    selected = set(page_numbers)
    if not selected:
        return result
    result_pages = [page for page in result.pages if page.page in selected]
    result_words = [word for word in result.words if word.page in selected]
    confidences = [word.confidence for word in result_words if word.confidence is not None]
    return DocumentOcrResult(
        pages=result_pages,
        words=result_words,
        confidence=(sum(confidences) / len(confidences) if confidences else result.confidence),
        method=result.method,
        diagnostics=result.diagnostics,
    )


def _document_ocr_engine(
    *,
    ocr_engine: RegionOcrEngine | None,
    document_ocr_engine: DocumentOcrEngine | None,
) -> DocumentOcrEngine:
    if document_ocr_engine is not None:
        return document_ocr_engine
    if ocr_engine is not None and hasattr(ocr_engine, "ocr_document"):
        return ocr_engine  # type: ignore[return-value]
    return TesseractRegionOcrEngine()


def _ocr_pages_to_parser_pages(ocr_result: DocumentOcrResult) -> list[dict[str, Any]]:
    return [
        {
            "page": page.page,
            "width": page.width,
            "height": page.height,
            "text": page.text,
            "ocr_confidence": round(page.confidence, 3) if page.confidence is not None else None,
            "source": "full_document_ocr",
        }
        for page in ocr_result.pages
    ]


def _ocr_words_to_parser_words(ocr_result: DocumentOcrResult) -> list[Word]:
    return [
        Word(
            text=word.text,
            page=word.page,
            x0=word.x0,
            top=word.top,
            x1=word.x1,
            bottom=word.bottom,
            confidence=word.confidence,
        )
        for word in ocr_result.words
    ]


def _tag_full_document_ocr_fields(value: Any) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("method"), str):
            value["method"] = f"full_document_ocr:{value['method']}"
        for child in value.values():
            _tag_full_document_ocr_fields(child)
    elif isinstance(value, list):
        for child in value:
            _tag_full_document_ocr_fields(child)


def _calibrate_ocr_field_confidences(value: Any, words: list[Word]) -> None:
    if isinstance(value, dict):
        bbox = value.get("bbox")
        page = value.get("page")
        if (
            isinstance(bbox, list)
            and len(bbox) == 4
            and isinstance(page, int)
            and isinstance(value.get("confidence"), int | float)
        ):
            confidences = sorted(
                word.confidence
                for word in words
                if word.page == page
                and word.confidence is not None
                and bbox[0] <= (word.x0 + word.x1) / 2 <= bbox[2]
                and bbox[1] <= (word.top + word.bottom) / 2 <= bbox[3]
            )
            if confidences:
                percentile_index = min(len(confidences) - 1, max(0, round((len(confidences) - 1) * 0.2)))
                ocr_confidence = float(confidences[percentile_index])
                value["confidence"] = round(min(float(value["confidence"]), ocr_confidence), 3)
                value["ocr_word_confidence"] = round(ocr_confidence, 3)
        for child in value.values():
            _calibrate_ocr_field_confidences(child, words)
    elif isinstance(value, list):
        for child in value:
            _calibrate_ocr_field_confidences(child, words)


def _merge_ocr_fields(
    fields: dict[str, Any],
    ocr_fields: dict[str, Any],
    *,
    target_fields: Iterable[str],
) -> list[str]:
    applied: list[str] = []
    targets = set(target_fields)
    for key in OCR_FALLBACK_FIELDS:
        ocr_value = ocr_fields.get(key)
        if not _has_normalized_field(ocr_fields, key):
            continue
        current = fields.get(key)
        if _has_normalized_field(fields, key):
            if isinstance(current, list) and key not in targets:
                continue
            if not _should_replace_with_full_ocr(current, ocr_value):
                continue
        fields[key] = ocr_value
        applied.append(key)
    return applied


def _should_replace_with_full_ocr(current: Any, ocr_value: Any) -> bool:
    if not isinstance(ocr_value, dict | list):
        return False
    if isinstance(current, list) or isinstance(ocr_value, list):
        return isinstance(ocr_value, list) and bool(ocr_value) and ocr_value != current
    if not isinstance(current, dict) or not isinstance(ocr_value, dict):
        return True
    if _field_comparison_value(current) == _field_comparison_value(ocr_value):
        return bool(_field_ambiguity_reasons(current))
    current_confidence = _normalized_confidence(current.get("confidence")) or 0.0
    ocr_confidence = _normalized_confidence(ocr_value.get("confidence")) or 0.0
    if _field_ambiguity_reasons(current):
        return ocr_confidence >= OCR_AMBIGUOUS_MIN_CONFIDENCE and ocr_confidence >= current_confidence - 0.05
    if current_confidence < OCR_CONFIDENCE_THRESHOLD:
        return ocr_confidence >= current_confidence
    return ocr_confidence >= OCR_CONFIDENCE_THRESHOLD and ocr_confidence >= current_confidence


def _field_comparison_value(field: dict[str, Any]) -> str:
    value = field.get("amount", field.get("value", field.get("raw")))
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _missing_required_normalized_fields(fields: dict[str, Any]) -> list[str]:
    return [
        key
        for key in REQUIRED_NORMALIZED_FIELDS
        if not _has_required_normalized_field(fields, key)
    ]


def _ocr_escalation_fields(
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
) -> list[str]:
    targets: list[str] = []
    for key in OCR_FALLBACK_FIELDS:
        value = fields.get(key)
        if not _has_normalized_field(fields, key):
            if key in REQUIRED_NORMALIZED_FIELDS or _pages_mention_field(pages, key):
                targets.append(key)
            continue
        if isinstance(value, dict):
            confidence = _normalized_confidence(value.get("confidence"))
            if confidence is None or confidence < OCR_CONFIDENCE_THRESHOLD or _field_ambiguity_reasons(value):
                targets.append(key)
        elif key == "line_items" and _line_items_need_review(value):
            targets.append(key)
    return targets


def _adaptive_ocr_escalation_fields(
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
    profiles: list[PageProfile],
) -> list[str]:
    degraded_pages = any(profile.route != "native_text" for profile in profiles)
    validation_failures = set(blocking_validation_failures(validate_invoice_fields(fields)))
    targets: list[str] = []
    for key in OCR_FALLBACK_FIELDS:
        value = fields.get(key)
        missing = not _has_normalized_field(fields, key)
        if missing:
            if key in REQUIRED_NORMALIZED_FIELDS or (degraded_pages and _pages_mention_field(pages, key)):
                targets.append(key)
            continue
        if isinstance(value, dict):
            material_reasons = [
                reason
                for reason in _field_ambiguity_reasons(value)
                if reason != "weak_geometry"
            ]
            confidence = _normalized_confidence(value.get("confidence"))
            if (
                key in REQUIRED_NORMALIZED_FIELDS or degraded_pages
            ) and (
                material_reasons
                or confidence is None
                or confidence < PARSER_REVIEW_CONFIDENCE_THRESHOLD
            ):
                targets.append(key)
        elif key == "line_items" and (
            degraded_pages or "line_item_sum" in validation_failures
        ):
            targets.append(key)
    if "amount_composition" in validation_failures:
        for key in ("subtotal", "discount", "tax", "shipping", "paid", "balance_due"):
            if key not in targets:
                targets.append(key)
    if "currency_consistency" in validation_failures and "currency" not in targets:
        targets.append("currency")
    if "date_order" in validation_failures:
        for key in ("issue_date", "due_date"):
            if key not in targets:
                targets.append(key)
    return targets


def _pages_mention_field(pages: list[dict[str, Any]], key: str) -> bool:
    if key == "line_items":
        labels = ("description", "item", "quantity", "qty", "unit price", "amount")
    else:
        labels = LABELS.get(key, (key.replace("_", " "),))
    page_text = "\n".join(str(page.get("text") or "").casefold() for page in pages)
    return any(label.casefold() in page_text for label in labels)


def _important_ocr_pages(
    pages: list[dict[str, Any]],
    *,
    target_fields: Iterable[str],
    max_pages: int | None,
) -> list[int]:
    available: list[int] = []
    for page in pages:
        try:
            page_number = int(page["page"])
        except (KeyError, TypeError, ValueError):
            continue
        if page_number not in available:
            available.append(page_number)
    if not available:
        return []

    selected: list[int] = []
    targets = list(target_fields)
    for page in pages:
        try:
            page_number = int(page["page"])
        except (KeyError, TypeError, ValueError):
            continue
        page_view = [page]
        if any(_pages_mention_field(page_view, key) for key in targets) and page_number not in selected:
            selected.append(page_number)
    for page_number in (available[0], available[-1], *available):
        if page_number not in selected:
            selected.append(page_number)
    if max_pages is not None:
        return selected[: max(0, max_pages)]
    return selected


def _field_ambiguity_reasons(field: dict[str, Any]) -> list[str]:
    reasons = field.get("ambiguity_reasons")
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons if reason]


def _line_items_need_review(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    for item in value:
        if not isinstance(item, dict):
            continue
        for role in ("description", "quantity", "unit_price", "amount"):
            field = item.get(role)
            if not isinstance(field, dict):
                continue
            confidence = _normalized_confidence(field.get("confidence"))
            if confidence is None or confidence < OCR_CONFIDENCE_THRESHOLD or _field_ambiguity_reasons(field):
                return True
    return False


def _has_required_normalized_field(fields: dict[str, Any], key: str) -> bool:
    return _has_normalized_field(fields, key)


def _has_normalized_field(fields: dict[str, Any], key: str) -> bool:
    value = fields.get(key)
    if key == "line_items":
        return isinstance(value, list) and bool(value)
    if not isinstance(value, dict):
        return False
    if key in {"subtotal", "discount", "tax", "shipping", "paid", "balance_due"}:
        return value.get("amount") is not None
    normalized_value = value.get("value")
    return normalized_value is not None and normalized_value != ""


def _append_warning_once(warnings: list[str], warning: str) -> None:
    if warning not in warnings:
        warnings.append(warning)


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
        "ocr_used": False,
        "ocr_parts": [],
        "normal_model_failed_parts": list(REQUIRED_NORMALIZED_FIELDS)
        if status in {"needs_review", "no_text_layer"}
        else [],
        "ocr_failed_parts": list(REQUIRED_NORMALIZED_FIELDS) if status == "needs_review" else [],
    }
    if status == "needs_review":
        result["review"] = {
            "required": True,
            "reason": "missing_required_normalized_data",
            "missing_fields": list(REQUIRED_NORMALIZED_FIELDS),
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
    _resolve_date_candidates(fields)

    fields["seller"] = _party_field(lines, "seller")
    fields["buyer"] = _party_field(lines, "buyer")
    fields["payment_instructions"] = _payment_field(lines)

    inferred_currency = fields["currency"]["value"] if isinstance(fields.get("currency"), dict) else None
    for key in ("subtotal", "discount", "tax", "shipping", "paid", "balance_due"):
        fields[key] = _best_money_field(lines, key, inferred_currency)

    fields["line_items"] = _line_items_from_word_tables(words, lines, inferred_currency)
    _resolve_amount_candidates(fields)
    _add_line_item_total_warnings(fields, warnings)

    return fields


def _resolve_amount_candidates(fields: dict[str, Any]) -> None:
    balance_due = fields.get("balance_due")
    if not isinstance(balance_due, dict):
        return
    subtotal = _field_amount(fields.get("subtotal"))
    if subtotal is None:
        subtotal = _line_item_amount_sum(fields.get("line_items"))
    if subtotal is None:
        return

    expected = (
        subtotal
        - abs(_field_amount(fields.get("discount")) or 0.0)
        + (_field_amount(fields.get("tax")) or 0.0)
        + (_field_amount(fields.get("shipping")) or 0.0)
        - (_field_amount(fields.get("paid")) or 0.0)
    )
    candidates = [balance_due]
    alternatives = balance_due.get("alternatives")
    if isinstance(alternatives, list):
        candidates.extend(candidate for candidate in alternatives if isinstance(candidate, dict))
    candidates_with_amounts = [
        (candidate, amount)
        for candidate in candidates
        if (amount := _field_amount(candidate)) is not None
    ]
    if not candidates_with_amounts:
        return
    selected, selected_amount = min(
        candidates_with_amounts,
        key=lambda item: abs(item[1] - expected),
    )
    tolerance = max(0.02, abs(expected) * 0.001)
    if abs(selected_amount - expected) > tolerance:
        return

    resolved = dict(selected)
    remaining = [
        dict(candidate)
        for candidate, amount in candidates_with_amounts
        if candidate is not selected and amount != selected_amount
    ]
    if remaining:
        resolved["alternatives"] = remaining[:5]
    else:
        resolved.pop("alternatives", None)
    reasons = [
        reason
        for reason in _field_ambiguity_reasons(resolved)
        if reason not in {"multiple_totals", "weak_geometry"}
    ]
    if reasons:
        resolved["ambiguity_reasons"] = reasons
    else:
        resolved.pop("ambiguity_reasons", None)
    resolved["confidence"] = max(float(resolved.get("confidence") or 0.0), 0.93)
    resolved["method"] = f"constraint_resolved:{resolved.get('method') or 'candidate'}"
    resolved["resolution"] = {
        "rule": "amount_composition",
        "expected": round(expected, 2),
    }
    fields["balance_due"] = resolved


def _resolve_date_candidates(fields: dict[str, Any]) -> None:
    issue_field = fields.get("issue_date")
    due_field = fields.get("due_date")
    if not isinstance(issue_field, dict) or not isinstance(due_field, dict):
        return
    issue_candidates = _possible_dates(str(issue_field.get("raw") or ""))
    due_candidates = _possible_dates(str(due_field.get("raw") or ""))
    if not issue_candidates or not due_candidates:
        return
    valid_pairs = [
        (issue, due)
        for issue in issue_candidates
        for due in due_candidates
        if 0 <= (due - issue).days <= 366
    ]
    if not valid_pairs:
        return
    issue, due = min(
        valid_pairs,
        key=lambda pair: (
            0 if (pair[1] - pair[0]).days <= 120 else 1,
            (pair[1] - pair[0]).days,
        ),
    )
    _apply_resolved_date(issue_field, issue, issue_candidates)
    _apply_resolved_date(due_field, due, due_candidates)


def _possible_dates(raw: str) -> list[date]:
    digits = re.sub(r"\D", "", raw)
    candidates: list[date] = []
    if len(digits) == 8 and digits.startswith(("19", "20")):
        parsed = _safe_date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
        if parsed:
            candidates.append(parsed)
    elif len(digits) in {6, 8}:
        first = int(digits[:2])
        second = int(digits[2:4])
        year = int(digits[4:])
        if len(digits) == 6:
            year = _normalize_year(year)
        for month, day in ((second, first), (first, second)):
            parsed = _safe_date(year, month, day)
            if parsed and parsed not in candidates:
                candidates.append(parsed)
    else:
        separated = re.search(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", raw)
        if separated:
            first, second, year = (int(part) for part in separated.groups())
            year = _normalize_year(year)
            for month, day in ((second, first), (first, second)):
                parsed = _safe_date(year, month, day)
                if parsed and parsed not in candidates:
                    candidates.append(parsed)
    return candidates


def _apply_resolved_date(field: dict[str, Any], selected: date, candidates: list[date]) -> None:
    selected_value = selected.isoformat()
    alternatives = [candidate.isoformat() for candidate in candidates if candidate != selected]
    if field.get("value") != selected_value:
        field["value"] = selected_value
        field["method"] = f"date_order_resolved:{field.get('method') or 'candidate'}"
        field["confidence"] = max(float(field.get("confidence") or 0.0), 0.93)
    if alternatives:
        field["date_alternatives"] = alternatives


def _field_amount(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("amount", value.get("value"))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _line_item_amount_sum(value: Any) -> float | None:
    if not isinstance(value, list) or not value:
        return None
    amounts = [
        _field_amount(item.get("amount"))
        for item in value
        if isinstance(item, dict)
    ]
    if len(amounts) != len(value) or any(amount is None for amount in amounts):
        return None
    return sum(amount for amount in amounts if amount is not None)


def _best_scalar_field(lines: list[Line], key: str) -> dict[str, Any] | None:
    identifier_key = key in {"invoice_number", "purchase_order"}
    candidates: list[dict[str, Any]] = []
    for line in lines:
        matches = _label_matches(line, LABELS[key])
        for match in matches:
            if _is_shadowed_by_longer_known_label(line, match):
                continue
            if identifier_key:
                identifier = _identifier_after_label(lines, line, match, key=key)
                if identifier is None:
                    continue
                raw, value, bbox, method = identifier
            else:
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
            elif not identifier_key:
                raw = _trim_at_known_label(raw)
                value = _normalize_scalar_value(key, raw)
                if not value:
                    continue

            confidence = 0.92
            if len(_label_tokens(match.label)) >= 2:
                confidence += 0.03
            if key == "invoice_number" and _looks_like_invoice_number(str(value)):
                confidence += 0.03
            if identifier_key:
                confidence += 0.02
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

    return _best_candidate(
        candidates,
        ambiguity_reason="multiple_scalar_values",
        ambiguity_confidence_ceiling=0.95,
    )


def _identifier_after_label(
    lines: list[Line],
    line: Line,
    match: LabelMatch,
    *,
    key: str,
) -> tuple[str, str, list[float] | None, str] | None:
    same_line_words = _words_after_label_before_next_field(line, match)
    identifier = _leading_identifier_word_span(same_line_words)
    if identifier is not None:
        raw, value, selected_words = identifier
        return raw, value, _bbox_for_words(selected_words), "same_line"

    if same_line_words:
        return None

    anchor_x = line.words[match.start].x0
    nearby: list[tuple[float, float, str, str, tuple[Word, ...]]] = []
    line_mid = (line.top + line.bottom) / 2
    for candidate in lines:
        if candidate is line or candidate.page != line.page or _matches_any_known_label(candidate):
            continue
        candidate_mid = (candidate.top + candidate.bottom) / 2
        vertical_distance = abs(candidate_mid - line_mid)
        identifier = _nearest_identifier_word_span(candidate.words, anchor_x=anchor_x)
        if identifier is None:
            continue
        raw, value, selected_words = identifier
        if key == "invoice_number" and re.match(r"^PO[-#/:]", value, re.IGNORECASE):
            continue
        nearby.append(
            (
                vertical_distance,
                abs(selected_words[0].x0 - anchor_x),
                raw,
                value,
                selected_words,
            )
        )
    if not nearby:
        return None
    _vertical, _horizontal, raw, value, selected_words = min(
        nearby,
        key=lambda item: (item[0], item[1]),
    )
    method = "nearby_above" if selected_words[0].top < line.top else "nearby_below"
    return raw, value, _bbox_for_words(selected_words), method


def _words_after_label_before_next_field(line: Line, match: LabelMatch) -> tuple[Word, ...]:
    boundary = len(line.words)
    for labels in LABELS.values():
        for other in _label_matches(line, labels):
            if other.start >= match.end:
                boundary = min(boundary, other.start)
    return tuple(word for word in line.words[match.end:boundary] if _canonical_token(word.text))


def _leading_identifier_word_span(
    words: tuple[Word, ...],
) -> tuple[str, str, tuple[Word, ...]] | None:
    if not words:
        return None
    value = _identifier_value(words[0].text)
    if value is not None:
        return words[0].text, value, words[:1]
    if len(words) >= 2 and _is_identifier_prefix(words[0].text):
        raw = _words_text(words[:2])
        value = _identifier_value(raw)
        if value is not None:
            return raw, value, words[:2]
    return None


def _nearest_identifier_word_span(
    words: tuple[Word, ...],
    *,
    anchor_x: float,
) -> tuple[str, str, tuple[Word, ...]] | None:
    candidates: list[tuple[float, str, str, tuple[Word, ...]]] = []
    for index, word in enumerate(words):
        span = _leading_identifier_word_span(words[index:])
        if span is None:
            continue
        raw, value, selected_words = span
        candidates.append((abs(selected_words[0].x0 - anchor_x), raw, value, selected_words))
    if not candidates:
        return None
    _, raw, value, selected_words = min(candidates, key=lambda item: item[0])
    return raw, value, selected_words


def _next_visual_line(lines: list[Line], line: Line) -> Line | None:
    return next(
        (
            candidate
            for candidate in lines
            if candidate.page == line.page and candidate.top > line.bottom
        ),
        None,
    )


def _is_identifier_prefix(raw: str) -> bool:
    return _canonical_token(raw) in {"bill", "cm", "doc", "fy", "inv", "invoice", "job", "po"}


def _is_shadowed_by_longer_known_label(line: Line, match: LabelMatch) -> bool:
    for labels in LABELS.values():
        for other in _label_matches(line, labels):
            if other.start == match.start and other.end > match.end:
                return True
    return False


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
    return _best_candidate(
        candidates,
        ambiguity_reason="multiple_totals" if key == "balance_due" else "multiple_amounts",
    )


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
        return _best_candidate(candidates, ambiguity_reason="label_collision")
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
                if "#" in label and not _hash_label_matches_words(line.words, index, len(label_tokens)):
                    continue
                matches.append(LabelMatch(label, index, index + len(label_tokens)))
    return matches


def _hash_label_matches_words(words: tuple[Word, ...], start: int, token_count: int) -> bool:
    end = min(len(words), start + token_count)
    if any("#" in word.text for word in words[start:end]):
        return True
    return end < len(words) and words[end].text.strip() == "#"


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
    return _best_candidate(candidates, ambiguity_reason="multiple_payment_blocks")


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
        row_words = _trim_overlapping_total_panel_words(row_words, header)
        if row_words and all(word.x0 >= header.region_x1 - 12.0 for word in row_words):
            continue
        if row_words:
            rows.append(_make_word_table_row(row_words, header))
    return rows


def _trim_overlapping_total_panel_words(
    words: tuple[Word, ...],
    header: TableHeader,
) -> tuple[Word, ...]:
    if not words:
        return words
    ordered = tuple(sorted(words, key=lambda word: word.x0))
    first_money_index = _first_money_like_word_index(ordered)
    if first_money_index >= len(ordered):
        return ordered
    for start, _end in _total_label_spans(ordered):
        if start <= first_money_index:
            continue
        label_x0 = ordered[start].x0
        if label_x0 >= header.line.x0 + (header.region_x1 - header.line.x0) * 0.6:
            return ordered[:start]
    return ordered


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
    value = re.sub(r"\s+", " ", text).strip(" :-")
    tokens = value.split()
    while tokens and _is_currency_word(tokens[-1]):
        tokens.pop()
    return " ".join(tokens).strip(" :-")


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
    matches = _label_matches(
        line,
        sorted(labels, key=lambda item: len(_label_tokens(item)), reverse=True),
    )
    return matches[0] if matches else None


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
    field = {
        "raw": raw,
        "value": value,
        "page": page,
        "bbox": bbox,
        "label": label,
        "confidence": round(confidence, 3),
        "method": method,
    }
    if confidence < OCR_CONFIDENCE_THRESHOLD:
        field["ambiguity_reasons"] = ["weak_geometry"]
    return field


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
    field = {
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
    if confidence < OCR_CONFIDENCE_THRESHOLD:
        field["ambiguity_reasons"] = ["weak_geometry"]
    return field


def _replace_field_with_ocr_result(
    fields: dict[str, Any],
    field: dict[str, Any],
    candidate: OcrRegionCandidate,
    ocr_text: RegionOcrText,
) -> bool | str:
    text = _clean_field_raw(ocr_text.text)
    if not text:
        return "empty_ocr_text"

    field_key = _ocr_field_key(candidate.path)
    if field_key in {"invoice_number", "purchase_order"} and candidate.reason == "missing_field":
        # A broad fallback region has no word-to-label association. Let the
        # full-document OCR path rebuild layout instead of guessing an ID.
        return "ocr_identifier_requires_document_layout"

    rejection_reason = _ocr_replacement_rejection_reason(field, ocr_text)
    if rejection_reason:
        return rejection_reason

    if field_key == "currency":
        parsed_currency = _currency_from_ocr_text(text)
        if parsed_currency is None:
            return "ocr_currency_unparseable"
        raw, value = parsed_currency
        field["raw"] = raw
        field["value"] = value
    elif field_key in {"issue_date", "due_date"}:
        parsed_date = _parse_date(text)
        if parsed_date is None:
            return "ocr_date_unparseable"
        raw, value = parsed_date
        field["raw"] = raw
        field["value"] = value
    elif field_key in {"subtotal", "discount", "tax", "shipping", "paid", "balance_due", "unit_price", "amount"}:
        money_values = _parse_money_values(text)
        if not money_values:
            return "ocr_money_unparseable"
        money = money_values[-1]
        inferred_currency = _field_currency(field) or _top_level_currency(fields)
        field["raw"] = money.raw
        field["value"] = round(money.amount, 2)
        field["amount"] = round(money.amount, 2)
        field["currency"] = _currency_for_money(money, inferred_currency)
    elif field_key == "quantity":
        quantity = _quantity_from_ocr_text(text)
        if quantity is None:
            return "ocr_quantity_unparseable"
        field["raw"] = text
        field["value"] = quantity
    elif field_key == "line_items":
        _replace_line_item_row_with_ocr_text(field, text)
    else:
        raw = _clean_ocr_text_for_field(field_key, text)
        value = _normalize_ocr_scalar_value(field_key, raw)
        if value is None:
            return "ocr_scalar_unusable"
        field["raw"] = raw
        field["value"] = value

    field["page"] = candidate.page
    field["bbox"] = _rounded_ocr_bbox(candidate)
    field["method"] = ocr_text.method
    if ocr_text.confidence is not None:
        field["confidence"] = round(ocr_text.confidence, 3)
    field.pop("ambiguity_reasons", None)
    _sync_line_item_value_after_ocr(fields, candidate.path)
    return True


def _ocr_replacement_rejection_reason(field: dict[str, Any], ocr_text: RegionOcrText) -> str | None:
    ocr_confidence = _normalized_confidence(ocr_text.confidence)
    if ocr_confidence is None:
        return "ocr_confidence_missing"
    has_usable_value = _has_usable_field_value(field)
    ambiguous = bool(_field_ambiguity_reasons(field))
    minimum_confidence = (
        OCR_AMBIGUOUS_MIN_CONFIDENCE
        if ambiguous or not has_usable_value
        else OCR_CONFIDENCE_THRESHOLD
    )
    if ocr_confidence < minimum_confidence:
        return "ocr_confidence_below_threshold"
    original_confidence = _normalized_confidence(field.get("confidence"))
    if (
        original_confidence is not None
        and has_usable_value
        and not ambiguous
        and ocr_confidence < original_confidence
    ):
        return "ocr_confidence_not_improved"
    return None


def _normalized_confidence(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0:
        return None
    if confidence > 1 and confidence <= 100:
        return confidence / 100
    return confidence


def _has_usable_field_value(field: dict[str, Any]) -> bool:
    for key in ("value", "raw", "amount"):
        value = field.get(key)
        if value is not None and value != "":
            return True
    return False


def _ocr_field_key(path: tuple[str | int, ...]) -> str | None:
    if not path:
        return None
    if path[0] != "line_items":
        return str(path[0])
    for part in reversed(path):
        if isinstance(part, str) and part != "line_items":
            return part
    return "line_items"


def _currency_from_ocr_text(text: str) -> tuple[str, str] | None:
    for match in re.finditer(CURRENCY_PATTERN, text, re.IGNORECASE):
        raw = match.group(0)
        currency = _normalize_currency(raw)
        if currency is not None:
            return raw, currency
    for money in _parse_money_values(text):
        if money.currency is not None:
            return money.raw, money.currency
    return None


def _quantity_from_ocr_text(text: str) -> int | float | None:
    for token in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        quantity = _parse_quantity_token(token)
        if quantity is not None:
            return quantity
    return None


def _field_currency(field: dict[str, Any]) -> str | None:
    currency = field.get("currency")
    return currency if isinstance(currency, str) else None


def _top_level_currency(fields: dict[str, Any]) -> str | None:
    currency = fields.get("currency")
    if not isinstance(currency, dict):
        return None
    value = currency.get("value")
    return value if isinstance(value, str) else None


def _normalize_ocr_scalar_value(field_key: str | None, raw: str) -> Any:
    if field_key == "invoice_number":
        return _normalize_scalar_value("invoice_number", _trim_at_known_label(raw))
    if field_key == "purchase_order":
        return _normalize_scalar_value("purchase_order", _trim_at_known_label(raw))
    if field_key == "terms":
        return _normalize_scalar_value("terms", _trim_at_known_label(raw))
    if field_key == "payment_instructions":
        return _normalize_scalar_value("payment_instructions", raw)
    return raw if raw else None


def _clean_ocr_text_for_field(field_key: str | None, text: str) -> str:
    raw = _clean_line_item_description(text) if field_key == "description" else text
    if field_key in {"invoice_number", "purchase_order", "terms", "payment_instructions", "seller", "buyer"}:
        raw = _strip_leading_field_label(field_key, raw)
    return raw


def _strip_leading_field_label(field_key: str | None, raw: str) -> str:
    if not field_key or field_key not in LABELS:
        return raw.strip(" :")
    value = raw.strip()
    for label in sorted(LABELS[field_key], key=len, reverse=True):
        pattern = _leading_label_pattern(label)
        if pattern is None:
            continue
        stripped = pattern.sub("", value, count=1).strip(" :")
        if stripped != value:
            return stripped
    return value.strip(" :")


def _leading_label_pattern(label: str) -> re.Pattern[str] | None:
    tokens = re.findall(r"[a-z0-9]+", label, re.IGNORECASE)
    if not tokens:
        return None
    separator = r"[\s:#./_-]*"
    return re.compile(
        r"^\s*" + separator.join(re.escape(token) for token in tokens) + r"\s*[:#./_-]?\s*",
        re.IGNORECASE,
    )


def _replace_line_item_row_with_ocr_text(field: dict[str, Any], text: str) -> None:
    field["row_raw"] = text
    money_values = _parse_money_values(text)
    if money_values:
        description = _description_before_amount(text, money_values[-1].raw)
        field["raw"] = _clean_line_item_description(description) or field.get("raw") or text
    else:
        field["raw"] = _clean_line_item_description(text) or text


def _rounded_ocr_bbox(candidate: OcrRegionCandidate) -> list[float]:
    return [round(item, 2) for item in candidate.padded_bbox]


def _sync_line_item_value_after_ocr(fields: dict[str, Any], path: tuple[str | int, ...]) -> None:
    if len(path) < 3 or path[0] != "line_items" or not isinstance(path[1], int):
        return
    line_items = fields.get("line_items")
    if not isinstance(line_items, list) or path[1] >= len(line_items):
        return
    item = line_items[path[1]]
    if not isinstance(item, dict):
        return
    value = item.get("value")
    if not isinstance(value, dict):
        return
    role = path[-1]
    child = item.get(role)
    if not isinstance(role, str) or not isinstance(child, dict):
        return
    if role == "description":
        value["description"] = child.get("value")
        if isinstance(child.get("value"), str):
            item["raw"] = child["value"]
    elif role == "quantity":
        value["quantity"] = child.get("value")
    elif role == "unit_price":
        value["unit_price"] = child.get("amount")
    elif role == "amount":
        value["amount"] = child.get("amount")
        value["currency"] = child.get("currency")


def _best_candidate(
    candidates: list[dict[str, Any]],
    *,
    ambiguity_reason: str,
    ambiguity_confidence_ceiling: float | None = None,
) -> dict[str, Any] | None:
    if not candidates:
        return None
    ranked = sorted(
        candidates,
        key=lambda item: (
            float(item.get("confidence") or 0),
            float((item.get("bbox") or [0, 0, 0, 0])[3]),
        ),
        reverse=True,
    )
    selected = ranked[0]
    if len(ranked) > 1:
        selected_confidence = _normalized_confidence(selected.get("confidence")) or 0.0
        runner_up_confidence = _normalized_confidence(ranked[1].get("confidence")) or 0.0
        distinct_values = {
            _field_comparison_value(candidate)
            for candidate in ranked
            if _field_comparison_value(candidate)
        }
        if (
            len(distinct_values) > 1
            and (
                ambiguity_confidence_ceiling is None
                or selected_confidence < ambiguity_confidence_ceiling
            )
            and selected_confidence - runner_up_confidence <= 0.08
        ):
            reasons = _field_ambiguity_reasons(selected)
            if ambiguity_reason not in reasons:
                selected["ambiguity_reasons"] = [*reasons, ambiguity_reason]
        alternatives: list[dict[str, Any]] = []
        selected_value = _field_comparison_value(selected)
        for candidate in ranked[1:]:
            candidate_value = _field_comparison_value(candidate)
            if not candidate_value or candidate_value == selected_value:
                continue
            alternatives.append(
                {
                    key: value
                    for key, value in candidate.items()
                    if key not in {"alternatives", "ambiguity_reasons"}
                }
            )
        if alternatives:
            selected["alternatives"] = alternatives[:5]
    return selected


def _normalize_scalar_value(key: str, raw: str) -> str | None:
    strip_chars = " :\t\r\n" if key in {"invoice_number", "purchase_order"} else " :#\t\r\n"
    value = raw.strip(strip_chars)
    if not value:
        return None
    if key in {"invoice_number", "purchase_order"}:
        return _identifier_value(value)
    return value[:300]


def _identifier_value(raw: str) -> str | None:
    value = re.sub(r"\s*([#./:_-])\s*", r"\1", raw.strip())
    value = re.sub(
        r"\b(BILL|DOC|FY|INV|INVOICE|JOB|PO)\s+(?=\d)",
        r"\1-",
        value,
        flags=re.IGNORECASE,
    )
    parts = value.split()
    if len(parts) == 2 and _is_identifier_prefix(parts[0]):
        value = f"{parts[0]}-{parts[1]}"
        parts = [value]
    if len(parts) != 1:
        return None
    token = parts[0].strip(".:/_-")
    if not re.fullmatch(r"[#A-Za-z0-9][#A-Za-z0-9./:_-]*", token):
        return None
    if not any(character.isdigit() for character in token):
        return None
    return token[:300]


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
    for key in _missing_required_normalized_fields(fields):
        _append_warning_once(warnings, f"Could not confidently extract required normalized field {key}.")


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
