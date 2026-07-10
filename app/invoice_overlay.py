from __future__ import annotations

import argparse
import io
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .invoice_parser import parse_invoice_pdf


DEFAULT_HIGHLIGHT_COLOR = (1.0, 0.92, 0.0)
DEFAULT_HIGHLIGHT_OPACITY = 0.32
DEFAULT_HIGHLIGHT_PADDING = 1.25


@dataclass(frozen=True)
class HighlightBox:
    page: int
    bbox: tuple[float, float, float, float]
    source: str


def render_invoice_parse_overlay_pdf(
    content: bytes,
    parse_result: dict[str, Any],
    *,
    box_mode: str = "parsed",
    opacity: float = DEFAULT_HIGHLIGHT_OPACITY,
    padding: float = DEFAULT_HIGHLIGHT_PADDING,
    color: tuple[float, float, float] = DEFAULT_HIGHLIGHT_COLOR,
) -> bytes:
    """Return the original PDF with transparent yellow rectangles over selected parser boxes."""
    try:
        from pypdf import PdfReader, PdfWriter
        from pypdf.generic import (
            ArrayObject,
            DecodedStreamObject,
            DictionaryObject,
            FloatObject,
            NameObject,
        )
    except ImportError as exc:
        raise RuntimeError("Install pypdf to generate invoice parse overlay PDFs.") from exc

    reader = PdfReader(io.BytesIO(content))
    writer = PdfWriter()
    writer.pdf_header = "%PDF-1.4"
    writer.append_pages_from_reader(reader)

    boxes, page_dimensions = highlight_boxes_for_mode(content, parse_result, box_mode=box_mode)
    boxes_by_page = _boxes_by_page(boxes)
    graphics_state_ref = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/ExtGState"),
                NameObject("/ca"): FloatObject(_clamp(opacity, 0.05, 1.0)),
                NameObject("/CA"): FloatObject(_clamp(opacity, 0.05, 1.0)),
                NameObject("/BM"): NameObject("/Multiply"),
            }
        )
    )

    for page_index, page in enumerate(writer.pages, start=1):
        page_boxes = boxes_by_page.get(page_index)
        if not page_boxes:
            continue
        page_width, page_height = page_dimensions.get(
            page_index,
            (float(page.mediabox.width), float(page.mediabox.height)),
        )
        commands = _highlight_commands(
            page_boxes,
            page_width=page_width,
            page_height=page_height,
            page_left=float(page.mediabox.left),
            page_bottom=float(page.mediabox.bottom),
            padding=padding,
            color=color,
        )
        if not commands:
            continue

        resources = page.get(NameObject("/Resources"))
        if resources is None:
            resources = DictionaryObject()
            page[NameObject("/Resources")] = resources
        else:
            resources = resources.get_object()
        ext_gstate = resources.get(NameObject("/ExtGState"))
        if ext_gstate is None:
            ext_gstate = DictionaryObject()
            resources[NameObject("/ExtGState")] = ext_gstate
        else:
            ext_gstate = ext_gstate.get_object()
        ext_gstate[NameObject("/ZampHL")] = graphics_state_ref

        stream = DecodedStreamObject()
        stream.set_data("\n".join(commands).encode("ascii"))
        stream_ref = writer._add_object(stream)
        current_contents = page.get(NameObject("/Contents"))
        if current_contents is None:
            page[NameObject("/Contents")] = stream_ref
        elif isinstance(current_contents, ArrayObject):
            current_contents.append(stream_ref)
        else:
            page[NameObject("/Contents")] = ArrayObject([current_contents, stream_ref])

    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def parse_and_render_invoice_overlay_pdf(
    content: bytes,
    *,
    source_id: str | None = None,
    box_mode: str = "parsed",
    opacity: float = DEFAULT_HIGHLIGHT_OPACITY,
    padding: float = DEFAULT_HIGHLIGHT_PADDING,
) -> tuple[dict[str, Any], bytes]:
    parse_result = parse_invoice_pdf(content, source_id=source_id)
    overlay = render_invoice_parse_overlay_pdf(
        content,
        parse_result,
        box_mode=box_mode,
        opacity=opacity,
        padding=padding,
    )
    return parse_result, overlay


def highlight_boxes_for_mode(
    content: bytes,
    parse_result: dict[str, Any],
    *,
    box_mode: str,
) -> tuple[list[HighlightBox], dict[int, tuple[float, float]]]:
    page_dimensions = _page_dimensions(parse_result)
    if box_mode == "parsed":
        return iter_parse_bboxes(parse_result), page_dimensions
    if box_mode == "words":
        word_boxes, word_dimensions = extract_pdf_word_bboxes(content)
        return word_boxes, {**page_dimensions, **word_dimensions}
    if box_mode == "all":
        word_boxes, word_dimensions = extract_pdf_word_bboxes(content)
        return [*word_boxes, *iter_parse_bboxes(parse_result)], {**page_dimensions, **word_dimensions}
    raise ValueError("box_mode must be one of: parsed, words, all")


def iter_parse_bboxes(parse_result: dict[str, Any]) -> list[HighlightBox]:
    found: list[HighlightBox] = []
    seen: set[tuple[int, tuple[int, int, int, int], str]] = set()
    fields = parse_result.get("fields")
    _collect_bboxes(fields, path="fields", found=found, seen=seen)
    return found


def extract_pdf_word_bboxes(content: bytes) -> tuple[list[HighlightBox], dict[int, tuple[float, float]]]:
    try:
        import pdfplumber  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Install pdfplumber to extract PDF word boxes.") from exc

    boxes: list[HighlightBox] = []
    dimensions: dict[int, tuple[float, float]] = {}
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            dimensions[page_index] = (float(page.width), float(page.height))
            for word in page.extract_words(
                x_tolerance=1.5,
                y_tolerance=3,
                keep_blank_chars=False,
                use_text_flow=False,
            ):
                try:
                    bbox = (
                        float(word["x0"]),
                        float(word["top"]),
                        float(word["x1"]),
                        float(word["bottom"]),
                    )
                except (KeyError, TypeError, ValueError):
                    continue
                if bbox[2] > bbox[0] and bbox[3] > bbox[1]:
                    boxes.append(HighlightBox(page=page_index, bbox=bbox, source="word"))
    return boxes, dimensions


def _collect_bboxes(
    value: Any,
    *,
    path: str,
    found: list[HighlightBox],
    seen: set[tuple[int, tuple[int, int, int, int], str]],
) -> None:
    if isinstance(value, dict):
        page = value.get("page")
        bbox = value.get("bbox")
        if isinstance(page, int) and _is_bbox(bbox):
            normalized_bbox = tuple(float(item) for item in bbox)
            rounded = tuple(round(item * 100) for item in normalized_bbox)
            key = (page, rounded, _semantic_source(path))
            if key not in seen:
                seen.add(key)
                found.append(HighlightBox(page=page, bbox=normalized_bbox, source=_semantic_source(path)))
        for child_key, child_value in value.items():
            if child_key in {"pages", "warnings"}:
                continue
            _collect_bboxes(child_value, path=f"{path}.{child_key}", found=found, seen=seen)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _collect_bboxes(item, path=f"{path}[{index}]", found=found, seen=seen)


def _is_bbox(value: Any) -> bool:
    if not isinstance(value, list | tuple) or len(value) != 4:
        return False
    try:
        x0, top, x1, bottom = [float(item) for item in value]
    except (TypeError, ValueError):
        return False
    return x1 > x0 and bottom > top


def _semantic_source(path: str) -> str:
    source = path.removeprefix("fields.")
    if source.startswith("line_items["):
        if ".description" in source:
            return "line_items.description"
        if ".quantity" in source:
            return "line_items.quantity"
        if ".unit_price" in source:
            return "line_items.unit_price"
        if ".amount" in source:
            return "line_items.amount"
        return "line_items.row"
    return source.split(".", 1)[0]


def _boxes_by_page(boxes: Iterable[HighlightBox]) -> dict[int, list[HighlightBox]]:
    grouped: dict[int, list[HighlightBox]] = {}
    for box in boxes:
        grouped.setdefault(box.page, []).append(box)
    return grouped


def _page_dimensions(parse_result: dict[str, Any]) -> dict[int, tuple[float, float]]:
    dimensions: dict[int, tuple[float, float]] = {}
    for page in parse_result.get("pages") or []:
        if not isinstance(page, dict):
            continue
        try:
            page_number = int(page["page"])
            width = float(page["width"])
            height = float(page["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if width > 0 and height > 0:
            dimensions[page_number] = (width, height)
    return dimensions


def _highlight_commands(
    boxes: list[HighlightBox],
    *,
    page_width: float,
    page_height: float,
    page_left: float,
    page_bottom: float,
    padding: float,
    color: tuple[float, float, float],
) -> list[str]:
    red, green, blue = (_clamp(component, 0.0, 1.0) for component in color)
    commands = ["q", "/ZampHL gs", f"{red:.4f} {green:.4f} {blue:.4f} rg"]
    emitted = 0
    for box in boxes:
        rect = _pdf_rect(
            box.bbox,
            page_width=page_width,
            page_height=page_height,
            page_left=page_left,
            page_bottom=page_bottom,
            padding=padding,
        )
        if rect is None:
            continue
        x, y, width, height = rect
        commands.append(f"{x:.3f} {y:.3f} {width:.3f} {height:.3f} re f")
        emitted += 1
    commands.append("Q")
    return commands if emitted else []


def _pdf_rect(
    bbox: tuple[float, float, float, float],
    *,
    page_width: float,
    page_height: float,
    page_left: float,
    page_bottom: float,
    padding: float,
) -> tuple[float, float, float, float] | None:
    x0, top, x1, bottom = bbox
    x0 = _clamp(x0 - padding, 0.0, page_width)
    x1 = _clamp(x1 + padding, 0.0, page_width)
    top = _clamp(top - padding, 0.0, page_height)
    bottom = _clamp(bottom + padding, 0.0, page_height)
    if x1 <= x0 or bottom <= top:
        return None
    pdf_x = page_left + x0
    pdf_y = page_bottom + page_height - bottom
    return pdf_x, pdf_y, x1 - x0, bottom - top


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a PDF with invoice parser bbox highlights.")
    parser.add_argument("input_pdf", type=Path, help="Input digitally-born invoice PDF.")
    parser.add_argument("output_pdf", type=Path, help="Output PDF with yellow parse overlays.")
    parser.add_argument(
        "--boxes",
        choices=("parsed", "words", "all"),
        default="parsed",
        help=(
            "Which boxes to highlight: accepted parsed evidence, every extracted word box, "
            "or both. Defaults to parsed."
        ),
    )
    parser.add_argument("--opacity", type=float, default=DEFAULT_HIGHLIGHT_OPACITY)
    parser.add_argument("--padding", type=float, default=DEFAULT_HIGHLIGHT_PADDING)
    args = parser.parse_args()

    content = args.input_pdf.read_bytes()
    parse_result, overlay = parse_and_render_invoice_overlay_pdf(
        content,
        source_id=str(args.input_pdf),
        box_mode=args.boxes,
        opacity=args.opacity,
        padding=args.padding,
    )
    args.output_pdf.parent.mkdir(parents=True, exist_ok=True)
    args.output_pdf.write_bytes(overlay)
    boxes, _ = highlight_boxes_for_mode(content, parse_result, box_mode=args.boxes)
    print(f"Wrote {args.output_pdf} with {len(boxes)} highlighted {args.boxes} boxes.")
    if parse_result.get("warnings"):
        print("Warnings:")
        for warning in parse_result["warnings"]:
            print(f"- {warning}")


if __name__ == "__main__":
    main()
