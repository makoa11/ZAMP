from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable, Protocol


OCR_CONFIDENCE_THRESHOLD = 0.85
OCR_REGION_PADDING = 4.0
OCR_RENDER_DPI = 300
OCR_MAX_REGIONS = 8
OCR_TIMEOUT_SECONDS = 5.0


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
    def ocr_document(self, content: bytes) -> DocumentOcrResult:
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
    ) -> None:
        self.dpi = dpi
        self.language = language
        self.config = config
        self.timeout_seconds = timeout_seconds

    def ocr_region(
        self,
        content: bytes,
        *,
        page: int,
        bbox: tuple[float, float, float, float],
    ) -> RegionOcrText:
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install PyMuPDF to render PDF regions for OCR.") from exc

        try:
            import pytesseract  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install Pillow and pytesseract to run region OCR.") from exc

        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:  # pragma: no cover - exercised only with real PDF renderer failures.
            raise RegionOcrError(f"Could not open PDF for OCR: {exc}") from exc

        try:
            if page < 1 or page > document.page_count:
                raise RegionOcrError(f"OCR page {page} is outside the PDF page range.")
            pdf_page = document.load_page(page - 1)
            page_rect = pdf_page.rect
            clip = fitz.Rect(
                max(bbox[0], page_rect.x0),
                max(bbox[1], page_rect.y0),
                min(bbox[2], page_rect.x1),
                min(bbox[3], page_rect.y1),
            )
            if clip.is_empty or clip.is_infinite:
                raise RegionOcrError(f"OCR region on page {page} is empty after clipping.")
            zoom = self.dpi / 72.0
            pixmap = pdf_page.get_pixmap(
                matrix=fitz.Matrix(zoom, zoom),
                clip=clip,
                alpha=False,
                colorspace=fitz.csRGB,
            )
        except RegionOcrError:
            raise
        except Exception as exc:  # pragma: no cover - exercised only with real PDF renderer failures.
            raise RegionOcrError(f"Could not render OCR region on page {page}: {exc}") from exc
        finally:
            document.close()

        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        try:
            data_kwargs = {
                "lang": self.language,
                "config": self.config,
                "output_type": pytesseract.Output.DICT,
            }
            if self.timeout_seconds is not None:
                data_kwargs["timeout"] = self.timeout_seconds
            data = pytesseract.image_to_data(image, **data_kwargs)
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
                        image,
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

    def ocr_document(self, content: bytes) -> DocumentOcrResult:
        try:
            import fitz  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install PyMuPDF to render PDF pages for OCR.") from exc

        try:
            import pytesseract  # type: ignore[import-not-found]
            from PIL import Image  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RegionOcrUnavailable("Install Pillow and pytesseract to run full-document OCR.") from exc

        try:
            document = fitz.open(stream=content, filetype="pdf")
        except Exception as exc:  # pragma: no cover - exercised only with real PDF renderer failures.
            raise RegionOcrError(f"Could not open PDF for OCR: {exc}") from exc

        pages: list[DocumentOcrPage] = []
        words: list[DocumentOcrWord] = []
        confidences: list[float] = []
        try:
            zoom = self.dpi / 72.0
            matrix = fitz.Matrix(zoom, zoom)
            for page_index in range(document.page_count):
                pdf_page = document.load_page(page_index)
                page_number = page_index + 1
                page_rect = pdf_page.rect
                try:
                    pixmap = pdf_page.get_pixmap(
                        matrix=matrix,
                        alpha=False,
                        colorspace=fitz.csRGB,
                    )
                except Exception as exc:  # pragma: no cover - exercised only with real PDF renderer failures.
                    raise RegionOcrError(f"Could not render OCR page {page_number}: {exc}") from exc

                image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                try:
                    data_kwargs = {
                        "lang": self.language,
                        "config": self.config,
                        "output_type": pytesseract.Output.DICT,
                    }
                    if self.timeout_seconds is not None:
                        data_kwargs["timeout"] = self.timeout_seconds
                    data = pytesseract.image_to_data(image, **data_kwargs)
                except Exception as exc:
                    if exc.__class__.__name__ == "TesseractNotFoundError":
                        raise RegionOcrUnavailable("Install the tesseract OCR binary to run full-document OCR.") from exc
                    raise RegionOcrError(f"Tesseract failed on page {page_number}: {exc}") from exc

                page_words = _document_words_from_tesseract_data(data, page=page_number, zoom=zoom)
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
                pages.append(
                    DocumentOcrPage(
                        page=page_number,
                        width=float(page_rect.width),
                        height=float(page_rect.height),
                        text=_page_text_from_tesseract_data(data),
                        confidence=page_confidence,
                    )
                )
                words.extend(page_words)
        finally:
            document.close()

        confidence = sum(confidences) / len(confidences) if confidences else None
        return DocumentOcrResult(
            pages=pages,
            words=words,
            confidence=confidence,
            method="tesseract_document",
        )


def apply_low_confidence_region_ocr(
    content: bytes,
    *,
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
    warnings: list[str],
    threshold: float = OCR_CONFIDENCE_THRESHOLD,
    padding: float = OCR_REGION_PADDING,
    max_regions: int | None = OCR_MAX_REGIONS,
    engine: RegionOcrEngine | None = None,
    field_updater: RegionOcrFieldUpdater | None = None,
) -> dict[str, Any]:
    candidates = low_confidence_ocr_regions(
        fields=fields,
        pages=pages,
        threshold=threshold,
        padding=padding,
    )
    if max_regions is None:
        attempted_candidates = candidates
    else:
        attempted_candidates = candidates[: max(0, max_regions)]
    capped_region_count = len(candidates) - len(attempted_candidates)
    summary: dict[str, Any] = {
        "status": "skipped" if not candidates else "completed",
        "confidence_threshold": round(threshold, 3),
        "padding": round(padding, 2),
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
                summary["skipped_count"] += 1
        summary["regions"].append(region)

    return summary


def low_confidence_ocr_regions(
    *,
    fields: dict[str, Any],
    pages: list[dict[str, Any]],
    threshold: float = OCR_CONFIDENCE_THRESHOLD,
    padding: float = OCR_REGION_PADDING,
) -> list[OcrRegionCandidate]:
    page_dimensions = _page_dimensions(pages)
    found: list[OcrRegionCandidate] = []
    seen: set[tuple[str, int, tuple[int, int, int, int]]] = set()
    _collect_low_confidence_regions(
        fields,
        path=(),
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
) -> OcrRegionCandidate | None:
    confidence = _confidence_value(value.get("confidence"))
    if confidence is None or confidence >= threshold:
        return None
    try:
        page = int(value["page"])
    except (KeyError, TypeError, ValueError):
        return None
    bbox = _bbox_value(value.get("bbox"))
    if bbox is None:
        return None
    padded_bbox = _padded_bbox(bbox, padding=padding, dimensions=page_dimensions.get(page))
    if padded_bbox[2] <= padded_bbox[0] or padded_bbox[3] <= padded_bbox[1]:
        return None
    return OcrRegionCandidate(
        path=path,
        page=page,
        bbox=bbox,
        padded_bbox=padded_bbox,
        confidence=confidence,
    )


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
    padded = (
        max(0.0, x0 - padding),
        max(0.0, top - padding),
        x1 + padding,
        bottom + padding,
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
    }
    if field is not None:
        region["original_raw"] = field.get("raw")
        region["original_value"] = field.get("value")
        region["original_method"] = field.get("method")
    return region


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


def _document_words_from_tesseract_data(data: dict[str, Any], *, page: int, zoom: float) -> list[DocumentOcrWord]:
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
            x0 = float(left) / zoom
            y0 = float(top) / zoom
            x1 = (float(left) + float(width)) / zoom
            y1 = (float(top) + float(height)) / zoom
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
