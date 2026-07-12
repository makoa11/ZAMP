from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol


OCR_CONFIDENCE_THRESHOLD = 0.85
OCR_REGION_PADDING = 0.15
OCR_RENDER_DPI = 300
OCR_MAX_REGIONS = 8
OCR_MAX_DOCUMENT_PAGES: int | None = None
OCR_TIMEOUT_SECONDS = 5.0
OCR_REFINEMENT_DPI = 450


class RegionOcrUnavailable(RuntimeError):
    """Raised when optional OCR dependencies or the OCR binary are unavailable."""


class RegionOcrError(RuntimeError):
    """Raised when OCR dependencies exist but a specific region cannot be read."""


@dataclass(frozen=True)
class RegionOcrText:
    text: str
    confidence: float | None
    method: str


@dataclass(frozen=True)
class OcrRegionCandidate:
    path: tuple[str | int, ...]
    page: int
    bbox: tuple[float, float, float, float]
    padded_bbox: tuple[float, float, float, float]
    confidence: float
    reason: str = "low_confidence"


@dataclass(frozen=True)
class DocumentOcrWord:
    text: str
    page: int
    x0: float
    top: float
    x1: float
    bottom: float
    confidence: float | None


@dataclass(frozen=True)
class DocumentOcrPage:
    page: int
    width: float
    height: float
    text: str
    confidence: float | None


@dataclass(frozen=True)
class DocumentOcrResult:
    pages: list[DocumentOcrPage]
    words: list[DocumentOcrWord]
    confidence: float | None
    method: str
    diagnostics: dict[str, Any] = field(default_factory=dict)


class RegionOcrEngine(Protocol):
    def ocr_region(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float],
    ) -> RegionOcrText:
        ...


class DocumentOcrEngine(Protocol):
    def ocr_document(
        self,
        content: bytes,
        *,
        pages: Iterable[int] | None = None,
        max_pages: int | None = OCR_MAX_DOCUMENT_PAGES,
    ) -> DocumentOcrResult:
        ...


RegionOcrFieldUpdater = Callable[
    [dict[str, Any], OcrRegionCandidate, RegionOcrText],
    bool | str | None,
]


class TesseractRegionOcrEngine:
    def __init__(
        self,
        *,
        dpi: int = OCR_RENDER_DPI,
        language: str = "eng",
        config: str = "--psm 6",
        timeout_seconds: float | None = OCR_TIMEOUT_SECONDS,
        refinement_dpi: int = OCR_REFINEMENT_DPI,
        adaptive: bool = True,
        document_timeout_seconds: float | None = 90.0,
    ) -> None:
        self.dpi = dpi
        self.language = language
        self.config = config
        self.timeout_seconds = timeout_seconds
        self.refinement_dpi = refinement_dpi
        self.adaptive = adaptive
        self.document_timeout_seconds = document_timeout_seconds
        self.prefers_document_first = adaptive
        self._content_digest: str | None = None
        self._page_cache: dict[tuple[int, int], tuple[Any, float, float]] = {}

    def ocr_region(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float],
    ) -> RegionOcrText:
        try:
            import pytesseract  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install Pillow and pytesseract to run region OCR.") from exc

        image, page_width, page_height = self._render_page(
            content,
            page=page,
            dpi=self.refinement_dpi,
        )
        clipped = (
            max(0.0, bbox[0]),
            max(0.0, bbox[1]),
            min(page_width, bbox[2]),
            min(page_height, bbox[3]),
        )
        if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
            raise RegionOcrError(f"OCR region on page {page} is empty after clipping.")
        scale_x = image.width / page_width
        scale_y = image.height / page_height
        image = image.crop(
            (
                round(clipped[0] * scale_x),
                round(clipped[1] * scale_y),
                round(clipped[2] * scale_x),
                round(clipped[3] * scale_y),
            )
        )
        prepared, _diagnostics, _adaptive_config = _prepare_ocr_image(image, adaptive=self.adaptive)
        try:
            data_kwargs = {
                "lang": self.language,
                "config": _merge_tesseract_config(self.config, "--psm 7" if self.adaptive else ""),
                "output_type": pytesseract.Output.DICT,
            }
            if self.timeout_seconds is not None:
                data_kwargs["timeout"] = self.timeout_seconds
            data = pytesseract.image_to_data(prepared, **data_kwargs)
        except Exception as exc:
            if exc.__class__.__name__ == "TesseractNotFoundError":
                raise RegionOcrUnavailable("Install the tesseract OCR binary to run region OCR.") from exc
            raise RegionOcrError(f"Tesseract failed on page {page}: {exc}") from exc

        text, confidence = _text_from_tesseract_data(data)
        if not text:
            try:
                string_kwargs = {
                    "lang": self.language,
                    "config": self.config,
                }
                if self.timeout_seconds is not None:
                    string_kwargs["timeout"] = self.timeout_seconds
                text = (
                    pytesseract.image_to_string(
                        prepared,
                        **string_kwargs,
                    )
                    or ""
                ).strip()
            except Exception as exc:
                if exc.__class__.__name__ == "TesseractNotFoundError":
                    raise RegionOcrUnavailable("Install the tesseract OCR binary to run region OCR.") from exc
                raise RegionOcrError(f"Tesseract failed on page {page}: {exc}") from exc

        return RegionOcrText(
            text=text,
            confidence=confidence,
            method="tesseract_region",
        )

    def ocr_document(
        self,
        content: bytes,
        *,
        pages: Iterable[int] | None = None,
        max_pages: int | None = OCR_MAX_DOCUMENT_PAGES,
    ) -> DocumentOcrResult:
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install PyMuPDF to render PDF pages for OCR.") from exc

        try:
            import pytesseract  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install Pillow and pytesseract to run full-document OCR.") from exc

        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:  # pragma: no cover - exercised only with real PDF renderer failures.
            raise RegionOcrError(f"Could not open PDF for OCR: {exc}") from exc

        result_pages: list[DocumentOcrPage] = []
        words: list[DocumentOcrWord] = []
        confidences: list[float] = []
        page_diagnostics: list[dict[str, Any]] = []
        started_at = time.perf_counter()
        budget_exhausted = False
        try:
            zoom = self.dpi / 72.0
            page_numbers = _document_page_numbers(
                document.page_count,
                pages=pages,
                max_pages=max_pages,
            )
            for page_number in page_numbers:
                if (
                    self.document_timeout_seconds is not None
                    and time.perf_counter() - started_at >= self.document_timeout_seconds
                ):
                    budget_exhausted = True
                    break
                image, _page_width, _page_height = self._render_page(
                    content,
                    page=page_number,
                    dpi=self.dpi,
                )
                image, orientation_diagnostics = _correct_page_orientation(
                    image,
                    pytesseract=pytesseract,
                    timeout_seconds=self.timeout_seconds,
                    enabled=self.adaptive,
                )
                prepared, diagnostics, adaptive_config = _prepare_ocr_image(
                    image,
                    adaptive=self.adaptive,
                )
                page_diagnostics.append(
                    {"page": page_number, **orientation_diagnostics, **diagnostics}
                )
                try:
                    data_kwargs = {
                        "lang": self.language,
                        "config": _merge_tesseract_config(self.config, adaptive_config),
                        "output_type": pytesseract.Output.DICT,
                    }
                    if self.timeout_seconds is not None:
                        data_kwargs["timeout"] = self.timeout_seconds
                    data = pytesseract.image_to_data(prepared, **data_kwargs)
                except Exception as exc:
                    if exc.__class__.__name__ == "TesseractNotFoundError":
                        raise RegionOcrUnavailable("Install the tesseract OCR binary to run full-document OCR.") from exc
                    raise RegionOcrError(f"Tesseract failed on page {page_number}: {exc}") from exc

                page_words = _document_words_from_tesseract_data(
                    data,
                    page=page_number,
                    zoom=zoom,
                )
                page_confidences = [
                    word.confidence
                    for word in page_words
                    if word.confidence is not None
                ]
                confidences.extend(page_confidences)
                page_confidence = (
                    sum(page_confidences) / len(page_confidences)
                    if page_confidences
                    else None
                )
                result_pages.append(
                    DocumentOcrPage(
                        page=page_number,
                        width=prepared.width / zoom,
                        height=prepared.height / zoom,
                        text=_page_text_from_tesseract_data(data),
                        confidence=page_confidence,
                    )
                )
                words.extend(page_words)
        finally:
            document.close()

        confidence = sum(confidences) / len(confidences) if confidences else None
        return DocumentOcrResult(
            pages=result_pages,
            words=words,
            confidence=confidence,
            method="tesseract_adaptive_document" if self.adaptive else "tesseract_document",
            diagnostics={
                "render_cache_entries": len(self._page_cache),
                "page_preprocessing": page_diagnostics,
                "requested_pages": page_numbers,
                "processed_pages": [page.page for page in result_pages],
                "document_budget_exhausted": budget_exhausted,
            },
        )

    def _render_page(
        self,
        content: bytes,
        *,
        page: int,
        dpi: int,
    ) -> tuple[Any, float, float]:
        try:
            import fitz  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install PyMuPDF and Pillow to render PDF pages for OCR.") from exc

        digest = hashlib.sha256(content).hexdigest()
        if digest != self._content_digest:
            self._content_digest = digest
            self._page_cache.clear()
        cache_key = (page, dpi)
        cached = self._page_cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            document = fitz.open(stream=content, filetype="pdf")
            if page < 1 or page > document.page_count:
                raise RegionOcrError(f"OCR page {page} is outside the PDF page range.")
            pdf_page = document.load_page(page - 1)
            page_rect = pdf_page.rect
            pixmap = pdf_page.get_pixmap(
                matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0),
                alpha=False,
                colorspace=fitz.csRGB,
            )
            rendered = (
                Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples),
                float(page_rect.width),
                float(page_rect.height),
            )
        except RegionOcrError:
            raise
        except Exception as exc:  # pragma: no cover - renderer-specific failure.
            raise RegionOcrError(f"Could not render OCR page {page}: {exc}") from exc
        finally:
            if "document" in locals():
                document.close()
        self._page_cache[cache_key] = rendered
        return rendered


def apply_low_confidence_region_ocr(
    content: bytes,
    *,
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
    warnings: list[str],
    threshold: float = OCR_CONFIDENCE_THRESHOLD,
    padding: float = OCR_REGION_PADDING,
    max_regions: int | None = OCR_MAX_REGIONS,
    target_fields: Iterable[str] = (),
    only_target_fields: bool = False,
    engine: RegionOcrEngine | None = None,
    field_updater: RegionOcrFieldUpdater | None = None,
) -> dict[str, Any]:
    candidates = low_confidence_ocr_regions(
        fields=fields,
        pages=pages,
        threshold=threshold,
        padding=padding,
        target_fields=target_fields,
        only_target_fields=only_target_fields,
    )
    if max_regions is None:
        attempted_candidates = candidates
    else:
        attempted_candidates = candidates[: max(0, max_regions)]
    capped_region_count = len(candidates) - len(attempted_candidates)
    summary: dict[str, Any] = {
        "status": "skipped" if not candidates else "completed",
        "confidence_threshold": round(threshold, 3),
        "padding_ratio": round(padding, 3),
        "padding_percent": round(padding * 100, 1),
        "max_regions": max_regions,
        "candidate_count": len(candidates),
        "attempted_count": 0,
        "applied_count": 0,
        "skipped_count": capped_region_count,
        "failed_count": 0,
        "capped_region_count": capped_region_count,
        "regions": [],
    }
    if not candidates:
        summary["reason"] = "no_low_confidence_regions"
        return summary
    if not attempted_candidates:
        summary["status"] = "skipped"
        summary["reason"] = "max_regions_reached"
        return summary

    ocr_engine = engine or TesseractRegionOcrEngine()
    for candidate in attempted_candidates:
        field = _field_at_path(fields, candidate.path)
        region = _region_summary(candidate, field)
        summary["attempted_count"] += 1
        try:
            ocr_text = ocr_engine.ocr_region(
                content,
                page=candidate.page,
                bbox=candidate.padded_bbox,
            )
        except RegionOcrUnavailable as exc:
            message = f"Region OCR unavailable: {exc}"
            warnings.append(message)
            region["applied"] = False
            region["reason"] = "ocr_unavailable"
            region["error"] = str(exc)
            summary["regions"].append(region)
            summary["status"] = "unavailable"
            summary["reason"] = str(exc)
            summary["failed_count"] += 1
            return summary
        except Exception as exc:
            message = f"Region OCR failed for {_format_path(candidate.path)}: {exc}"
            warnings.append(message)
            region["applied"] = False
            region["reason"] = "ocr_failed"
            region["error"] = str(exc)
            summary["regions"].append(region)
            summary["status"] = "partial"
            summary["failed_count"] += 1
            continue

        region["text"] = ocr_text.text
        region["confidence"] = round(ocr_text.confidence, 3) if ocr_text.confidence is not None else None
        region["method"] = ocr_text.method
        created_field = False
        if field is None and len(candidate.path) == 1 and isinstance(candidate.path[0], str):
            field = {}
            fields[candidate.path[0]] = field
            created_field = True
        if field is None:
            region["applied"] = False
            region["reason"] = "field_missing"
            summary["skipped_count"] += 1
        else:
            updater = field_updater or _replace_field_with_ocr_text
            applied, reason = _normalize_update_result(updater(field, candidate, ocr_text))
            region["applied"] = applied
            if reason:
                region["reason"] = reason
            if applied:
                summary["applied_count"] += 1
            else:
                if created_field:
                    fields[candidate.path[0]] = None
                summary["skipped_count"] += 1
        summary["regions"].append(region)

    return summary


def low_confidence_ocr_regions(
    *,
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
    threshold: float = OCR_CONFIDENCE_THRESHOLD,
    padding: float = OCR_REGION_PADDING,
    target_fields: Iterable[str] = (),
    only_target_fields: bool = False,
) -> list[OcrRegionCandidate]:
    page_dimensions = _page_dimensions(pages)
    found: list[OcrRegionCandidate] = []
    seen: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    if not only_target_fields:
        _collect_low_confidence_regions(
            fields,
            path=(),
            page_dimensions=page_dimensions,
            threshold=threshold,
            padding=padding,
            found=found,
            seen=seen,
        )
    _collect_target_field_regions(
        fields=fields,
        pages=pages,
        target_fields=target_fields,
        page_dimensions=page_dimensions,
        threshold=threshold,
        padding=padding,
        found=found,
        seen=seen,
    )
    return sorted(found, key=_candidate_sort_key)


def _candidate_sort_key(candidate: OcrRegionCandidate) -> tuple[int, float, int, float, float, str]:
    return (
        _candidate_priority(candidate.path),
        candidate.confidence,
        candidate.page,
        candidate.padded_bbox[1],
        candidate.padded_bbox[0],
        _format_path(candidate.path),
    )


def _candidate_priority(path: tuple[str | int, ...]) -> int:
    key = _path_field_key(path)
    priorities = {
        "invoice_number": 0,
        "issue_date": 1,
        "due_date": 2,
        "balance_due": 3,
        "subtotal": 4,
        "tax": 5,
        "shipping": 6,
        "discount": 7,
        "paid": 8,
        "currency": 9,
        "purchase_order": 10,
        "terms": 11,
        "payment_instructions": 12,
        "seller": 13,
        "buyer": 14,
        "amount": 20,
        "unit_price": 21,
        "quantity": 22,
        "description": 23,
        "line_items": 24,
    }
    return priorities.get(key or "", 99)


def _path_field_key(path: tuple[str | int, ...]) -> str | None:
    if not path:
        return None
    if path[0] != "line_items":
        return str(path[0])
    for part in reversed(path):
        if isinstance(part, str) and part != "line_items":
            return part
    return "line_items"


def _collect_low_confidence_regions(
    value: Any,
    *,
    path: tuple[str | int, ...],
    page_dimensions: dict[int, tuple[float, float]],
    threshold: float,
    padding: float,
    found: list[OcrRegionCandidate],
    seen: set[tuple[str, int, tuple[int, int, int, int]]],
) -> None:
    if isinstance(value, dict):
        candidate = _candidate_from_field(
            value,
            path=path,
            page_dimensions=page_dimensions,
            threshold=threshold,
            padding=padding,
        )
        if candidate is not None:
            key = (
                _format_path(candidate.path),
                candidate.page,
                tuple(round(item * 100) for item in candidate.padded_bbox),
            )
            if key not in seen:
                seen.add(key)
                found.append(candidate)
        for child_key, child_value in value.items():
            if child_key == "ocr":
                continue
            _collect_low_confidence_regions(
                child_value,
                path=(*path, str(child_key)),
                page_dimensions=page_dimensions,
                threshold=threshold,
                padding=padding,
                found=found,
                seen=seen,
            )
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_low_confidence_regions(
                item,
                path=(*path, index),
                page_dimensions=page_dimensions,
                threshold=threshold,
                padding=padding,
                found=found,
                seen=seen,
            )


def _candidate_from_field(
    value: dict[str, Any],
    *,
    path: tuple[str | int, ...],
    page_dimensions: dict[int, tuple[float, float]],
    threshold: float,
    padding: float,
    minimum_padding: bool = False,
) -> OcrRegionCandidate | None:
    confidence = _confidence_value(value.get("confidence"))
    ambiguity_reasons = _ambiguity_reasons(value)
    if confidence is not None and confidence >= threshold and not ambiguity_reasons:
        return None
    try:
        page = int(value["page"])
    except (KeyError, TypeError, ValueError):
        return None
    bbox = _bbox_value(value.get("bbox"))
    if bbox is None:
        return None
    padded_bbox = _padded_bbox(bbox, padding=padding, dimensions=page_dimensions.get(page))
    if minimum_padding:
        padded_bbox = _minimum_padded_bbox(
            bbox,
            padded_bbox,
            dimensions=page_dimensions.get(page),
        )
    if padded_bbox[2] <= padded_bbox[0] or padded_bbox[3] <= padded_bbox[1]:
        return None
    return OcrRegionCandidate(
        path=path,
        page=page,
        bbox=bbox,
        padded_bbox=padded_bbox,
        confidence=confidence if confidence is not None else 0.0,
        reason=(
            "explicit_ambiguity"
            if ambiguity_reasons
            else "missing_confidence"
            if confidence is None
            else "low_confidence"
        ),
    )


def _collect_target_field_regions(
    *,
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
    target_fields: Iterable[str],
    page_dimensions: dict[int, tuple[float, float]],
    threshold: float,
    padding: float,
    found: list[OcrRegionCandidate],
    seen: set[tuple[str, int, tuple[int, int, int, int]]],
) -> None:
    existing_paths = {candidate.path for candidate in found}
    for field_key in target_fields:
        path = (str(field_key),)
        if path in existing_paths:
            continue
        field = fields.get(field_key)
        if isinstance(field, dict):
            candidate = _candidate_from_field(
                field,
                path=path,
                page_dimensions=page_dimensions,
                threshold=threshold,
                padding=padding,
                minimum_padding=True,
            )
            if candidate is not None:
                key = (
                    _format_path(path),
                    candidate.page,
                    tuple(round(item * 100) for item in candidate.padded_bbox),
                )
                if key not in seen:
                    seen.add(key)
                    found.append(candidate)
                continue
            confidence = _confidence_value(field.get("confidence"))
            reasons = _ambiguity_reasons(field)
            if confidence is not None and confidence >= threshold and not reasons:
                continue
            reason = (
                "explicit_ambiguity"
                if reasons
                else "missing_confidence"
                if confidence is None
                else "low_confidence"
            )
        elif field not in (None, [], ""):
            continue
        else:
            confidence = None
            reason = "missing_field"

        page, bbox = _likely_field_region(str(field_key), pages, page_dimensions)
        if page is None or bbox is None:
            continue
        padded_bbox = _padded_bbox(bbox, padding=padding, dimensions=page_dimensions.get(page))
        key = (
            _format_path(path),
            page,
            tuple(round(item * 100) for item in padded_bbox),
        )
        if key in seen:
            continue
        seen.add(key)
        found.append(
            OcrRegionCandidate(
                path=path,
                page=page,
                bbox=bbox,
                padded_bbox=padded_bbox,
                confidence=confidence if confidence is not None else 0.0,
                reason=reason,
            )
        )


def _likely_field_region(
    field_key: str,
    pages: list[dict[str, Any]],
    page_dimensions: dict[int, tuple[float, float]],
) -> tuple[int | None, tuple[float, float, float, float] | None]:
    if not page_dimensions:
        return None, None
    ordered_pages = [int(page["page"]) for page in pages if _valid_page_number(page, page_dimensions)]
    if not ordered_pages:
        ordered_pages = sorted(page_dimensions)
    hints = _FIELD_LABEL_HINTS.get(field_key, (field_key.replace("_", " "),))
    matching_pages = [
        int(page["page"])
        for page in pages
        if _valid_page_number(page, page_dimensions)
        and any(hint in str(page.get("text") or "").lower() for hint in hints)
    ]
    bottom_fields = {
        "subtotal", "discount", "tax", "shipping", "paid", "balance_due", "payment_instructions"
    }
    page = (matching_pages[-1] if field_key in bottom_fields else matching_pages[0]) if matching_pages else (
        ordered_pages[-1] if field_key in bottom_fields else ordered_pages[0]
    )
    width, height = page_dimensions[page]
    if field_key in bottom_fields:
        bbox = (width * 0.3, height * 0.4, width, height)
    elif field_key in {"seller", "buyer"}:
        bbox = (0.0, 0.0, width, height * 0.6)
    elif field_key == "line_items":
        bbox = (0.0, height * 0.12, width, height * 0.82)
    else:
        bbox = (0.0, 0.0, width, height * 0.42)
    return page, bbox


def _valid_page_number(
    page: dict[str, Any],
    page_dimensions: dict[int, tuple[float, float]],
) -> bool:
    try:
        return int(page["page"]) in page_dimensions
    except (KeyError, TypeError, ValueError):
        return False


def _ambiguity_reasons(field: dict[str, Any]) -> list[str]:
    reasons = field.get("ambiguity_reasons")
    if not isinstance(reasons, list):
        return []
    return [str(reason) for reason in reasons if reason]


def _page_dimensions(pages: list[dict[str, Any]]) -> dict[int, tuple[float, float]]:
    dimensions: dict[int, tuple[float, float]] = {}
    for page in pages:
        try:
            page_number = int(page["page"])
            width = float(page["width"])
            height = float(page["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            dimensions[page_number] = (width, height)
    return dimensions


def _confidence_value(value: Any) -> float | None:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    if confidence < 0:
        return None
    if confidence > 1 and confidence <= 100:
        return confidence / 100
    return confidence


def _bbox_value(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, list | tuple) or len(value) != 4:
        return None
    try:
        x0, top, x1, bottom = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or bottom <= top:
        return None
    return x0, top, x1, bottom


def _padded_bbox(
    bbox: tuple[float, float, float, float],
    *,
    padding: float,
    dimensions: tuple[float, float] | None,
) -> tuple[float, float, float, float]:
    x0, top, x1, bottom = bbox
    horizontal_padding = (x1 - x0) * max(0.0, padding)
    vertical_padding = (bottom - top) * max(0.0, padding)
    padded = (
        max(0.0, x0 - horizontal_padding),
        max(0.0, top - vertical_padding),
        x1 + horizontal_padding,
        bottom + vertical_padding,
    )
    if dimensions is None:
        return padded
    width, height = dimensions
    return (
        padded[0],
        padded[1],
        min(width, padded[2]),
        min(height, padded[3]),
    )


def _minimum_padded_bbox(
    bbox: tuple[float, float, float, float],
    padded_bbox: tuple[float, float, float, float],
    *,
    dimensions: tuple[float, float] | None,
) -> tuple[float, float, float, float]:
    x0, top, x1, bottom = bbox
    padded = (
        min(padded_bbox[0], max(0.0, x0 - 8.0)),
        min(padded_bbox[1], max(0.0, top - 4.0)),
        max(padded_bbox[2], x1 + 8.0),
        max(padded_bbox[3], bottom + 4.0),
    )
    if dimensions is None:
        return padded
    width, height = dimensions
    return padded[0], padded[1], min(width, padded[2]), min(height, padded[3])


def _field_at_path(fields: dict[str, Any], path: tuple[str | int, ...]) -> dict[str, Any] | None:
    target: Any = fields
    for part in path:
        try:
            target = target[part]
        except (KeyError, IndexError, TypeError):
            return None
    if not isinstance(target, dict):
        return None
    return target


def _region_summary(candidate: OcrRegionCandidate, field: dict[str, Any] | None) -> dict[str, Any]:
    region: dict[str, Any] = {
        "path": _format_path(candidate.path),
        "page": candidate.page,
        "bbox": _round_bbox(candidate.bbox),
        "padded_bbox": _round_bbox(candidate.padded_bbox),
        "original_confidence": round(candidate.confidence, 3),
        "candidate_reason": candidate.reason,
    }
    if field is not None:
        region["original_raw"] = field.get("raw")
        region["original_value"] = field.get("value")
        region["original_method"] = field.get("method")
    return region


def _document_page_numbers(
    page_count: int,
    *,
    pages: Iterable[int] | None,
    max_pages: int | None,
) -> list[int]:
    if pages is None:
        selected = list(range(1, page_count + 1))
    else:
        selected = []
        for raw_page in pages:
            try:
                page = int(raw_page)
            except (TypeError, ValueError):
                continue
            if 1 <= page <= page_count and page not in selected:
                selected.append(page)
    if max_pages is not None:
        selected = selected[: max(0, max_pages)]
    return selected


_FIELD_LABEL_HINTS: dict[str, tuple[str, ...]] = {
    "invoice_number": ("invoice no", "invoice #", "invoice number", "bill no"),
    "issue_date": ("invoice date", "issue date", "bill date"),
    "due_date": ("due date", "pay by", "payment before"),
    "purchase_order": ("purchase order", "po number", "po #", "po ref"),
    "terms": ("payment terms", "terms", "net "),
    "seller": ("seller", "supplier", "vendor", "from"),
    "buyer": ("bill to", "buyer", "customer", "client"),
    "subtotal": ("subtotal", "net amount"),
    "tax": ("tax", "vat", "gst"),
    "balance_due": ("balance due", "amount due", "grand total", "total"),
    "payment_instructions": ("payment instructions", "bank", "iban", "swift", "remit"),
    "line_items": ("description", "quantity", "qty", "unit price", "amount"),
}


def _normalize_update_result(result: bool | str | None) -> tuple[bool, str | None]:
    if result is True:
        return True, None
    if isinstance(result, str):
        return False, result
    return False, "updater_rejected"


def _replace_field_with_ocr_text(
    field: dict[str, Any],
    candidate: OcrRegionCandidate,
    ocr_text: RegionOcrText,
) -> bool | str:
    text = ocr_text.text.strip()
    if not text:
        return "empty_ocr_text"
    field["raw"] = text
    field["value"] = text
    field["bbox"] = _round_bbox(candidate.padded_bbox)
    field["method"] = ocr_text.method
    if ocr_text.confidence is not None:
        field["confidence"] = round(ocr_text.confidence, 3)
    return True


def _correct_page_orientation(
    image: Any,
    *,
    pytesseract: Any,
    timeout_seconds: float | None,
    enabled: bool,
) -> tuple[Any, dict[str, Any]]:
    if not enabled:
        return image, {"rotation": 0, "orientation_method": "disabled"}
    probe = image.copy()
    probe.thumbnail((1600, 1600))
    kwargs: dict[str, Any] = {"lang": "osd", "config": "--psm 0"}
    if timeout_seconds is not None:
        kwargs["timeout"] = timeout_seconds
    try:
        osd = pytesseract.image_to_osd(probe, **kwargs)
    except Exception:
        return image, {"rotation": 0, "orientation_method": "osd_unavailable"}
    match = re.search(r"Rotate:\s*(0|90|180|270)", str(osd))
    rotation = int(match.group(1)) if match else 0
    if rotation:
        image = image.rotate(-rotation, expand=True, fillcolor="white")
    return image, {"rotation": rotation, "orientation_method": "tesseract_osd"}


def _prepare_ocr_image(image: Any, *, adaptive: bool) -> tuple[Any, dict[str, Any], str]:
    if not adaptive:
        return image, {"variant": "original", "opencv": False}, ""
    try:
        import cv2  # type: ignore[import-not-found]
        import numpy as np  # type: ignore[import-not-found]
        from PIL import Image  # type: ignore[import-not-found]
    except ImportError:
        try:
            from PIL import ImageOps  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install Pillow to prepare images for OCR.") from exc
        return (
            ImageOps.autocontrast(image.convert("L")),
            {"variant": "autocontrast", "opencv": False},
            "--psm 6",
        )

    rgb = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    contrast = float(gray.std())
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    _threshold, foreground = cv2.threshold(
        enhanced,
        0,
        255,
        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
    )
    angle = _estimate_skew_angle(foreground, cv2=cv2, np=np)
    if 0.35 <= abs(angle) <= 5.0:
        height, width = enhanced.shape
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        enhanced = cv2.warpAffine(
            enhanced,
            matrix,
            (width, height),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
        _threshold, foreground = cv2.threshold(
            enhanced,
            0,
            255,
            cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU,
        )
    else:
        angle = 0.0

    ink_ratio = float(np.count_nonzero(foreground)) / float(foreground.size or 1)
    if contrast < 15.0:
        prepared = cv2.adaptiveThreshold(
            enhanced,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            35,
            15,
        )
        variant = "deskewed_adaptive_threshold" if angle else "adaptive_threshold"
    else:
        prepared = enhanced
        variant = "deskewed_clahe" if angle else "clahe"
    psm = 11 if ink_ratio < 0.025 else 4
    return (
        Image.fromarray(prepared),
        {
            "variant": variant,
            "opencv": True,
            "contrast_stddev": round(contrast, 2),
            "deskew_degrees": round(angle, 3),
            "ink_ratio": round(ink_ratio, 4),
            "psm": psm,
        },
        f"--psm {psm}",
    )


def _estimate_skew_angle(foreground: Any, *, cv2: Any, np: Any) -> float:
    coordinates = np.column_stack(np.where(foreground > 0))
    if len(coordinates) < 20:
        return 0.0
    angle = float(cv2.minAreaRect(coordinates[:, ::-1].astype("float32"))[-1])
    if angle < -45:
        angle = 90 + angle
    elif angle > 45:
        angle -= 90
    return angle


def _merge_tesseract_config(base: str, adaptive: str) -> str:
    if not adaptive:
        return base
    without_psm = re.sub(r"(?:^|\s)--psm\s+\d+", "", base).strip()
    return " ".join(part for part in (without_psm, adaptive) if part)


def _text_from_tesseract_data(data: dict[str, Any]) -> tuple[str, float | None]:
    words: list[str] = []
    confidences: list[float] = []
    texts = data.get("text") or []
    raw_confidences = data.get("conf") or []
    for text, confidence in zip(texts, raw_confidences):
        clean_text = str(text or "").strip()
        if not clean_text:
            continue
        words.append(clean_text)
        try:
            parsed_confidence = float(confidence)
        except (TypeError, ValueError):
            continue
        if parsed_confidence >= 0:
            confidences.append(parsed_confidence / 100 if parsed_confidence > 1 else parsed_confidence)

    average_confidence = None
    if confidences:
        average_confidence = sum(confidences) / len(confidences)
    return " ".join(words).strip(), average_confidence


def _document_words_from_tesseract_data(
    data: dict[str, Any],
    *,
    page: int,
    zoom: float,
    rotation: int = 0,
    original_pixel_size: tuple[int, int] | None = None,
) -> list[DocumentOcrWord]:
    words: list[DocumentOcrWord] = []
    texts = data.get("text") or []
    raw_confidences = data.get("conf") or []
    left_values = data.get("left") or []
    top_values = data.get("top") or []
    width_values = data.get("width") or []
    height_values = data.get("height") or []
    for text, confidence, left, top, width, height in zip(
        texts,
        raw_confidences,
        left_values,
        top_values,
        width_values,
        height_values,
    ):
        clean_text = str(text or "").strip()
        if not clean_text:
            continue
        try:
            pixel_bbox = (
                float(left),
                float(top),
                float(left) + float(width),
                float(top) + float(height),
            )
            if rotation and original_pixel_size is not None:
                pixel_bbox = _inverse_rotated_bbox(
                    pixel_bbox,
                    rotation=rotation,
                    original_pixel_size=original_pixel_size,
                )
            x0, y0, x1, y1 = (coordinate / zoom for coordinate in pixel_bbox)
        except (TypeError, ValueError):
            continue
        if x1 <= x0 or y1 <= y0:
            continue
        words.append(
            DocumentOcrWord(
                text=clean_text,
                page=page,
                x0=round(x0, 2),
                top=round(y0, 2),
                x1=round(x1, 2),
                bottom=round(y1, 2),
                confidence=_confidence_value(confidence),
            )
        )
    return words


def _inverse_rotated_bbox(
    bbox: tuple[float, float, float, float],
    *,
    rotation: int,
    original_pixel_size: tuple[int, int],
) -> tuple[float, float, float, float]:
    original_width, original_height = original_pixel_size
    x0, y0, x1, y1 = bbox
    corners = ((x0, y0), (x1, y0), (x0, y1), (x1, y1))
    mapped: list[tuple[float, float]] = []
    for x, y in corners:
        if rotation == 90:
            mapped.append((y, original_height - x))
        elif rotation == 180:
            mapped.append((original_width - x, original_height - y))
        elif rotation == 270:
            mapped.append((original_width - y, x))
        else:
            mapped.append((x, y))
    x_values = [point[0] for point in mapped]
    y_values = [point[1] for point in mapped]
    return min(x_values), min(y_values), max(x_values), max(y_values)


def _page_text_from_tesseract_data(data: dict[str, Any]) -> str:
    texts = data.get("text") or []
    blocks = data.get("block_num") or []
    paragraphs = data.get("par_num") or []
    lines = data.get("line_num") or []
    words = data.get("word_num") or []
    grouped: dict[tuple[int, int, int], list[tuple[int, str]]] = {}
    for index, text in enumerate(texts):
        clean_text = str(text or "").strip()
        if not clean_text:
            continue
        try:
            key = (int(blocks[index]), int(paragraphs[index]), int(lines[index]))
            word_number = int(words[index])
        except (IndexError, TypeError, ValueError):
            key = (0, 0, index)
            word_number = index
        grouped.setdefault(key, []).append((word_number, clean_text))
    return "\n".join(
        " ".join(text for _word_number, text in sorted(values, key=lambda item: item[0]))
        for _key, values in sorted(grouped.items())
    ).strip()


def _format_path(path: Iterable[str | int]) -> str:
    formatted = "fields"
    for part in path:
        if isinstance(part, int):
            formatted += f"[{part}]"
        else:
            formatted += f".{part}"
    return formatted


def _round_bbox(bbox: tuple[float, float, float, float]) -> list[float]:
    return [round(item, 2) for item in bbox]
