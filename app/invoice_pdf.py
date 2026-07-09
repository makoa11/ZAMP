from __future__ import annotations

from typing import Any


MM_TO_PT = 72 / 25.4


FONT_MAP = {
    "system": ("F1", "F2"),
    "serif": ("F3", "F4"),
    "slab": ("F3", "F4"),
    "mono": ("F5", "F6"),
    "condensed": ("F1", "F2"),
    "rounded": ("F1", "F2"),
    "formal": ("F3", "F4"),
    "industrial": ("F1", "F2"),
    "humanist": ("F1", "F2"),
    "geometric": ("F1", "F2"),
    "courier": ("F5", "F6"),
    "book": ("F3", "F4"),
    "narrow": ("F1", "F2"),
    "typewriter": ("F5", "F6"),
    "neo": ("F1", "F2"),
}

FONT_NAMES = {
    "F1": "Helvetica",
    "F2": "Helvetica-Bold",
    "F3": "Times-Roman",
    "F4": "Times-Bold",
    "F5": "Courier",
    "F6": "Courier-Bold",
}

HELVETICA_WIDTHS = {
    " ": 278,
    "!": 278,
    '"': 355,
    "#": 556,
    "$": 556,
    "%": 889,
    "&": 667,
    "'": 191,
    "(": 333,
    ")": 333,
    "*": 389,
    "+": 584,
    ",": 278,
    "-": 333,
    ".": 278,
    "/": 278,
    ":": 333,
    ";": 333,
    "<": 584,
    "=": 584,
    ">": 584,
    "?": 556,
    "@": 1015,
    "[": 278,
    "\\": 278,
    "]": 278,
    "^": 469,
    "_": 556,
    "`": 222,
    "{": 334,
    "|": 260,
    "}": 334,
    "~": 584,
    "€": 556,
    "£": 556,
    "¥": 556,
}
HELVETICA_WIDTHS.update(dict.fromkeys("0123456789", 556))
HELVETICA_WIDTHS.update(
    {
        "A": 667,
        "B": 667,
        "C": 722,
        "D": 722,
        "E": 667,
        "F": 611,
        "G": 778,
        "H": 722,
        "I": 278,
        "J": 500,
        "K": 667,
        "L": 556,
        "M": 833,
        "N": 722,
        "O": 778,
        "P": 667,
        "Q": 778,
        "R": 722,
        "S": 667,
        "T": 611,
        "U": 722,
        "V": 667,
        "W": 944,
        "X": 667,
        "Y": 667,
        "Z": 611,
        "a": 556,
        "b": 556,
        "c": 500,
        "d": 556,
        "e": 556,
        "f": 278,
        "g": 556,
        "h": 556,
        "i": 222,
        "j": 222,
        "k": 500,
        "l": 222,
        "m": 833,
        "n": 556,
        "o": 556,
        "p": 556,
        "q": 556,
        "r": 333,
        "s": 500,
        "t": 278,
        "u": 556,
        "v": 500,
        "w": 722,
        "x": 500,
        "y": 500,
        "z": 500,
    }
)

HELVETICA_BOLD_WIDTHS = {
    **HELVETICA_WIDTHS,
    "A": 722,
    "B": 722,
    "D": 722,
    "E": 667,
    "J": 556,
    "K": 722,
    "L": 611,
    "P": 667,
    "R": 722,
    "S": 667,
    "a": 556,
    "b": 611,
    "c": 556,
    "d": 611,
    "f": 333,
    "g": 611,
    "h": 611,
    "i": 278,
    "j": 278,
    "k": 556,
    "l": 278,
    "m": 889,
    "n": 611,
    "o": 611,
    "p": 611,
    "q": 611,
    "r": 389,
    "s": 556,
    "t": 333,
    "u": 611,
    "v": 556,
    "w": 778,
    "x": 556,
    "y": 556,
}

TIMES_WIDTHS = {
    " ": 250,
    "!": 333,
    '"': 408,
    "#": 500,
    "$": 500,
    "%": 833,
    "&": 778,
    "'": 180,
    "(": 333,
    ")": 333,
    "*": 500,
    "+": 564,
    ",": 250,
    "-": 333,
    ".": 250,
    "/": 278,
    ":": 278,
    ";": 278,
    "<": 564,
    "=": 564,
    ">": 564,
    "?": 444,
    "@": 921,
    "[": 333,
    "\\": 278,
    "]": 333,
    "^": 469,
    "_": 500,
    "`": 333,
    "{": 480,
    "|": 200,
    "}": 480,
    "~": 541,
    "€": 500,
    "£": 500,
    "¥": 500,
}
TIMES_WIDTHS.update(dict.fromkeys("0123456789", 500))
TIMES_WIDTHS.update(
    {
        "A": 722,
        "B": 667,
        "C": 667,
        "D": 722,
        "E": 611,
        "F": 556,
        "G": 722,
        "H": 722,
        "I": 333,
        "J": 389,
        "K": 722,
        "L": 611,
        "M": 889,
        "N": 722,
        "O": 722,
        "P": 556,
        "Q": 722,
        "R": 667,
        "S": 556,
        "T": 611,
        "U": 722,
        "V": 722,
        "W": 944,
        "X": 722,
        "Y": 722,
        "Z": 611,
        "a": 444,
        "b": 500,
        "c": 444,
        "d": 500,
        "e": 444,
        "f": 333,
        "g": 500,
        "h": 500,
        "i": 278,
        "j": 278,
        "k": 500,
        "l": 278,
        "m": 778,
        "n": 500,
        "o": 500,
        "p": 500,
        "q": 500,
        "r": 333,
        "s": 389,
        "t": 278,
        "u": 500,
        "v": 500,
        "w": 722,
        "x": 500,
        "y": 500,
        "z": 444,
    }
)

TIMES_BOLD_WIDTHS = {
    **TIMES_WIDTHS,
    "A": 722,
    "B": 667,
    "C": 722,
    "D": 722,
    "E": 667,
    "F": 611,
    "G": 778,
    "I": 389,
    "J": 500,
    "L": 667,
    "M": 944,
    "N": 722,
    "P": 611,
    "R": 722,
    "S": 556,
    "T": 667,
    "V": 722,
    "W": 1000,
    "X": 722,
    "Y": 722,
    "a": 500,
    "b": 556,
    "c": 444,
    "d": 556,
    "e": 444,
    "f": 333,
    "g": 500,
    "h": 556,
    "i": 278,
    "j": 333,
    "k": 556,
    "l": 278,
    "m": 833,
    "n": 556,
    "o": 500,
    "p": 556,
    "q": 556,
    "r": 444,
    "s": 389,
    "t": 333,
    "u": 556,
    "v": 500,
    "w": 722,
    "x": 500,
    "y": 500,
}

FONT_WIDTHS = {
    "F1": (HELVETICA_WIDTHS, 556),
    "F2": (HELVETICA_BOLD_WIDTHS, 556),
    "F3": (TIMES_WIDTHS, 500),
    "F4": (TIMES_BOLD_WIDTHS, 500),
    "F5": ({}, 600),
    "F6": ({}, 600),
}


def render_invoice_pdf(samples: list[dict[str, Any]]) -> bytes:
    pages = []
    for sample in samples:
        canvas = _PdfCanvas(
            width_pt=float(sample["paper"]["width_mm"]) * MM_TO_PT,
            height_pt=float(sample["paper"]["height_mm"]) * MM_TO_PT,
            font_style=str(sample["template"].get("font_style", "system")),
        )
        _render_invoice_page(canvas, sample)
        pages.append(canvas)
    return _build_pdf(pages)


class _PdfCanvas:
    def __init__(self, *, width_pt: float, height_pt: float, font_style: str) -> None:
        self.width_pt = width_pt
        self.height_pt = height_pt
        self.font_style = font_style
        self.commands: list[str] = []

    def rect(
        self,
        x_mm: float,
        y_mm: float,
        width_mm: float,
        height_mm: float,
        *,
        fill: str | None = None,
        stroke: str | None = None,
        line_width: float = 0.25,
    ) -> None:
        x = x_mm * MM_TO_PT
        y = self.height_pt - ((y_mm + height_mm) * MM_TO_PT)
        width = width_mm * MM_TO_PT
        height = height_mm * MM_TO_PT
        self.commands.append("q")
        self.commands.append(f"{line_width:.3f} w")
        if fill:
            self.commands.append(f"{_rgb(fill)} rg")
        if stroke:
            self.commands.append(f"{_rgb(stroke)} RG")
        op = "B" if fill and stroke else "f" if fill else "S"
        self.commands.append(f"{x:.2f} {y:.2f} {width:.2f} {height:.2f} re {op}")
        self.commands.append("Q")

    def line(
        self,
        x1_mm: float,
        y1_mm: float,
        x2_mm: float,
        y2_mm: float,
        *,
        color: str = "#111827",
        line_width: float = 0.3,
    ) -> None:
        x1 = x1_mm * MM_TO_PT
        y1 = self.height_pt - (y1_mm * MM_TO_PT)
        x2 = x2_mm * MM_TO_PT
        y2 = self.height_pt - (y2_mm * MM_TO_PT)
        self.commands.append("q")
        self.commands.append(f"{_rgb(color)} RG")
        self.commands.append(f"{line_width:.3f} w")
        self.commands.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")
        self.commands.append("Q")

    def text(
        self,
        x_mm: float,
        y_mm: float,
        text: str,
        *,
        size: float = 8,
        bold: bool = False,
        color: str = "#111827",
        align: str = "left",
        width_mm: float | None = None,
        min_size: float = 4.0,
    ) -> None:
        font_regular, font_bold = FONT_MAP.get(self.font_style, FONT_MAP["system"])
        font = font_bold if bold else font_regular
        value = _pdf_safe_text(text)
        if not value:
            return
        if width_mm:
            box_width = width_mm * MM_TO_PT
            size = _fit_font_size(value, size, box_width, min_size=min_size, font=font)
            value = _truncate_to_width(value, size, box_width, font=font)
        x = x_mm * MM_TO_PT
        y = self.height_pt - (y_mm * MM_TO_PT) - size
        if align != "left" and width_mm:
            estimated_width = _text_width(value, size, font=font)
            box_width = width_mm * MM_TO_PT
            if align == "right":
                x += max(0, box_width - estimated_width)
            elif align == "center":
                x += max(0, (box_width - estimated_width) / 2)
        self.commands.append("BT")
        self.commands.append(f"/{font} {size:.2f} Tf")
        self.commands.append(f"{_rgb(color)} rg")
        self.commands.append(f"{x:.2f} {y:.2f} Td")
        self.commands.append(f"({_escape_text(value)}) Tj")
        self.commands.append("ET")

    def wrapped_text(
        self,
        x_mm: float,
        y_mm: float,
        width_mm: float,
        text: str,
        *,
        size: float = 7,
        line_height_mm: float = 3.5,
        max_lines: int = 3,
        bold: bool = False,
        color: str = "#111827",
    ) -> None:
        font_regular, font_bold = FONT_MAP.get(self.font_style, FONT_MAP["system"])
        font = font_bold if bold else font_regular
        lines = _wrap_text(_pdf_safe_text(text), width_mm, size, max_lines=max_lines, font=font)
        for index, line in enumerate(lines):
            self.text(
                x_mm,
                y_mm + (index * line_height_mm),
                line,
                size=size,
                bold=bold,
                color=color,
            )

    def stream(self) -> bytes:
        return "\n".join(self.commands).encode("cp1252", errors="replace")


def _render_invoice_page(canvas: _PdfCanvas, sample: dict[str, Any]) -> None:
    canvas.rect(
        0,
        0,
        float(sample["paper"]["width_mm"]),
        float(sample["paper"]["height_mm"]),
        fill="#ffffff",
    )
    for component in sample["components"]:
        kind = component["kind"]
        if kind == "accent-band":
            canvas.rect(
                component["x_mm"],
                component["y_mm"],
                component["width_mm"],
                component["height_mm"],
                fill=sample["template"]["accent"],
            )
        elif kind == "accent-rail":
            canvas.rect(
                component["x_mm"],
                component["y_mm"],
                component["width_mm"],
                component["height_mm"],
                fill=sample["template"]["accent"],
            )
        elif kind == "company-header":
            _render_company_header(canvas, component, sample)
        elif kind == "logo":
            _render_logo(canvas, component, sample)
        elif kind == "title":
            _render_title(canvas, component, sample)
        elif kind in {"seller", "buyer"}:
            _render_entity(canvas, component, sample, kind)
        elif kind == "invoice-meta":
            _render_facts(canvas, component, _invoice_meta_facts(sample))
        elif kind == "dates":
            facts = _date_facts(sample)
            if component.get("variant") == "two-row":
                facts = facts[:2]
            _render_facts(canvas, component, facts)
        elif kind == "items-table":
            _render_table(canvas, component, sample)
        elif kind == "totals":
            _render_totals(canvas, component, sample)
        elif kind == "payment":
            _render_payment(canvas, component, sample)
        elif kind in {"terms", "footer", "remittance", "timeline"}:
            _render_note(canvas, component, sample, kind=kind, title=kind.replace("-", " ").title())
        elif kind in {
            "signature",
            "approver",
            "insurance",
            "work-order",
            "tax-summary",
            "packing",
            "quality",
            "schedule",
            "deposit",
            "itinerary",
            "sla",
        }:
            _render_note(canvas, component, sample, kind=kind, title=kind.replace("-", " ").title())
        elif kind == "stamp":
            _render_stamp(canvas, component, sample)
        elif kind == "barcode":
            _render_barcode(canvas, component, sample)


def _render_company_header(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any]) -> None:
    seller = sample["data"]["seller"]
    accent = sample["template"]["accent"]
    ink = sample["template"]["ink"]
    variant = str(component.get("variant", "split"))
    if variant in {"banded", "industrial"}:
        canvas.rect(
            component["x_mm"],
            component["y_mm"],
            component["width_mm"],
            component["height_mm"],
            fill=accent,
        )
        text_color = "#ffffff"
    else:
        canvas.rect(
            component["x_mm"],
            component["y_mm"],
            component["width_mm"],
            component["height_mm"],
            fill="#ffffff",
        )
        if "no-line" not in variant:
            canvas.line(
                component["x_mm"] + 4,
                component["y_mm"] + component["height_mm"],
                component["x_mm"] + component["width_mm"] - 4,
                component["y_mm"] + component["height_mm"],
                color=accent,
                line_width=1.2,
            )
        text_color = ink

    if "centered" in variant:
        canvas.text(
            component["x_mm"],
            component["y_mm"] + 2.2,
            seller["name"],
            size=10,
            bold=True,
            color=text_color,
            align="center",
            width_mm=component["width_mm"],
        )
        canvas.text(
            component["x_mm"],
            component["y_mm"] + 7.4,
            f'{seller["line1"]} / {seller["city"]} / {seller["email"]}',
            size=5.8,
            color=text_color,
            align="center",
            width_mm=component["width_mm"],
        )
        return

    canvas.rect(component["x_mm"] + 5, component["y_mm"] + 2, 7, 7, fill=accent)
    canvas.text(
        component["x_mm"] + 6.1,
        component["y_mm"] + 4.2,
        _initials(seller["name"]),
        size=5.5,
        bold=True,
        color="#ffffff",
    )
    canvas.text(component["x_mm"] + 15, component["y_mm"] + 2.2, seller["name"], size=8.5, bold=True, color=text_color)
    canvas.text(
        component["x_mm"] + 15,
        component["y_mm"] + 7.0,
        f'{seller["line1"]} / {seller["city"]}',
        size=5.6,
        color=text_color,
    )
    canvas.text(
        component["x_mm"] + component["width_mm"] - 74,
        component["y_mm"] + 2.8,
        seller["email"],
        size=5.6,
        color=text_color,
        align="right",
        width_mm=68,
    )
    canvas.text(
        component["x_mm"] + component["width_mm"] - 74,
        component["y_mm"] + 7.1,
        seller.get("tax_id", ""),
        size=5.6,
        color=text_color,
        align="right",
        width_mm=68,
    )


def _render_logo(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any]) -> None:
    seller = sample["data"]["seller"]
    accent = sample["template"]["accent"]
    canvas.rect(component["x_mm"], component["y_mm"], 11, 11, fill=accent)
    canvas.text(component["x_mm"] + 2.2, component["y_mm"] + 3.2, _initials(seller["name"]), size=6.5, bold=True, color="#ffffff")
    canvas.wrapped_text(
        component["x_mm"] + 13,
        component["y_mm"] + 1.2,
        max(10, component["width_mm"] - 13),
        seller["name"],
        size=6.8,
        line_height_mm=3.2,
        max_lines=2,
        bold=True,
    )


def _render_title(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any]) -> None:
    labels = sample["data"].get("labels", {})
    title_size = 17 if component["height_mm"] > 16 else 9.6 if component["height_mm"] <= 10.5 else 10.8
    canvas.text(
        component["x_mm"],
        component["y_mm"],
        str(labels.get("document_title", "Invoice")).upper(),
        size=title_size,
        bold=True,
        align="right",
        width_mm=component["width_mm"],
        color=sample["template"]["ink"],
        min_size=6.5,
    )
    canvas.text(
        component["x_mm"],
        component["y_mm"] + min(11, component["height_mm"] - 5),
        sample["template"]["name"],
        size=5.8,
        align="right",
        width_mm=component["width_mm"],
        color="#64748b",
    )


def _render_entity(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any], kind: str) -> None:
    labels = sample["data"].get("labels", {})
    entity = sample["data"]["seller"] if kind == "seller" else sample["data"]["buyer"]
    label = str(labels.get("seller" if kind == "seller" else "buyer", "From" if kind == "seller" else "Bill To"))
    y = component["y_mm"]
    canvas.text(component["x_mm"], y, label.upper(), size=5.8, bold=True, color=sample["template"]["accent"])
    canvas.wrapped_text(component["x_mm"], y + 4, component["width_mm"], entity["name"], size=7, bold=True, max_lines=1)
    canvas.wrapped_text(component["x_mm"], y + 8, component["width_mm"], entity["line1"], size=5.6, max_lines=1, color="#475569")
    canvas.wrapped_text(component["x_mm"], y + 11.5, component["width_mm"], entity["city"], size=5.6, max_lines=1, color="#475569")
    canvas.wrapped_text(component["x_mm"], y + 15, component["width_mm"], entity["email"], size=5.4, max_lines=1, color="#475569")
    if kind == "seller" and entity.get("tax_id") and component["height_mm"] > 25:
        canvas.wrapped_text(component["x_mm"], y + 18.5, component["width_mm"], entity["tax_id"], size=5.2, max_lines=1, color="#475569")


def _invoice_meta_facts(sample: dict[str, Any]) -> list[tuple[str, str]]:
    data = sample["data"]
    labels = data.get("labels", {})
    return [
        (str(labels.get("invoice_number", "No.")), str(data["invoice_number"])),
        (str(labels.get("purchase_order", "PO")), str(data["purchase_order"])),
        (str(labels.get("status", "Status")), str(data["status"])),
    ]


def _date_facts(sample: dict[str, Any]) -> list[tuple[str, str]]:
    data = sample["data"]
    labels = data.get("labels", {})
    return [
        (str(labels.get("issue_date", "Issued")), str(data.get("issue_date_display", data["issue_date"]))),
        (str(labels.get("due_date", "Due")), str(data.get("due_date_display", data["due_date"]))),
        (str(labels.get("terms", "Terms")), str(data["terms"])),
    ]


def _render_facts(canvas: _PdfCanvas, component: dict[str, Any], facts: list[tuple[str, str]]) -> None:
    row_height = max(4.2, min(7, component["height_mm"] / max(1, len(facts))))
    compact = component["height_mm"] <= 16
    label_size = 5.1 if compact else 5.6
    value_size = 5.45 if compact else 6
    label_width = component["width_mm"] * (0.42 if component["width_mm"] > 65 else 0.48)
    gap = 2 if component["width_mm"] > 26 else 1
    value_x = component["x_mm"] + label_width + gap
    value_width = max(8, component["width_mm"] - label_width - gap)
    y = component["y_mm"]
    for label, value in facts:
        canvas.text(
            component["x_mm"],
            y + 0.8,
            label,
            size=label_size,
            bold=True,
            color="#64748b",
            width_mm=label_width,
            min_size=4.2,
        )
        canvas.text(
            value_x,
            y + 0.8,
            value,
            size=value_size,
            bold=True,
            align="right",
            width_mm=value_width,
            color="#111827",
            min_size=4.2,
        )
        canvas.line(component["x_mm"], y + row_height, component["x_mm"] + component["width_mm"], y + row_height, color="#cbd5e1", line_width=0.25)
        y += row_height


def _render_table(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any]) -> None:
    data = sample["data"]
    table = data.get("table", {})
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    if not columns:
        columns = [
            {"key": "item", "label": "Item"},
            {"key": "quantity", "label": "Qty", "numeric": True},
            {"key": "unit_price", "label": "Rate", "numeric": True},
            {"key": "amount", "label": "Amount", "numeric": True},
        ]
    rows = list(data["items"])
    has_total = bool(table.get("total_in_table"))
    row_count = len(rows) + (1 if has_total else 0)
    header_h = 6.4 if component["height_mm"] > 40 else 4.5
    row_h = max(3.6, (component["height_mm"] - header_h) / max(1, row_count))
    widths = _column_widths(columns, component["width_mm"])
    x = component["x_mm"]
    y = component["y_mm"]
    canvas.rect(x, y, component["width_mm"], component["height_mm"], stroke="#cbd5e1", line_width=0.25)
    canvas.rect(x, y, component["width_mm"], header_h, fill=sample["template"]["accent"])
    col_x = x
    for column, width in zip(columns, widths):
        canvas.text(
            col_x + 1.3,
            y + 1.7,
            str(column.get("label", "")),
            size=5.4,
            bold=True,
            color="#ffffff",
            width_mm=max(3, width - 2.2),
            min_size=4.0,
        )
        col_x += width
    y += header_h
    for row in rows:
        _render_table_row(canvas, x, y, widths, columns, row, data, row_h=row_h)
        canvas.line(x, y + row_h, x + component["width_mm"], y + row_h, color="#cbd5e1", line_width=0.2)
        y += row_h
    if has_total:
        _render_table_total_row(canvas, x, y, widths, columns, data, row_h=row_h, accent=sample["template"]["accent"])


def _render_table_row(
    canvas: _PdfCanvas,
    x: float,
    y: float,
    widths: list[float],
    columns: list[dict[str, Any]],
    row: dict[str, Any],
    data: dict[str, Any],
    *,
    row_h: float,
) -> None:
    col_x = x
    for column, width in zip(columns, widths):
        key = str(column.get("key", ""))
        value = _table_cell_text(row, key, data)
        align = "right" if column.get("numeric") else "left"
        size = 5.6 if row_h < 6 else 6.2
        if align == "right":
            canvas.text(col_x + 1.3, y + 1.0, value, size=size, align="right", width_mm=max(3, width - 2.2))
        else:
            canvas.wrapped_text(
                col_x + 1.3,
                y + 1.0,
                max(3, width - 2.2),
                value,
                size=size,
                line_height_mm=2.8,
                max_lines=2 if key == "item" else 1,
                bold=key in {"item", "item_plain"},
            )
        col_x += width


def _render_table_total_row(
    canvas: _PdfCanvas,
    x: float,
    y: float,
    widths: list[float],
    columns: list[dict[str, Any]],
    data: dict[str, Any],
    *,
    row_h: float,
    accent: str,
) -> None:
    canvas.rect(x, y, sum(widths), row_h, fill="#f8fafc")
    canvas.line(x, y, x + sum(widths), y, color=accent, line_width=1.1)
    label_index = _total_label_column_index(columns)
    quantity_index = _first_column_index(columns, {"quantity", "quantity_unit"})
    amount_index = _last_column_index(columns, {"amount", "taxable"})
    labels = data.get("labels", {})
    col_x = x
    for index, (column, width) in enumerate(zip(columns, widths)):
        text = ""
        if index == label_index:
            text = str(labels.get("balance_due", "TOTAL")).upper()
        elif index == quantity_index:
            text = str(data.get("total_quantity", ""))
        elif index == amount_index:
            text = _money(float(data.get("balance_due", 0)), data)
        align = "right" if column.get("numeric") else "left"
        canvas.text(col_x + 1.3, y + max(1.1, row_h / 2 - 1.6), text, size=6.1, bold=True, align=align, width_mm=max(3, width - 2.2))
        col_x += width


def _render_totals(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any]) -> None:
    data = sample["data"]
    labels = data.get("labels", {})
    rows = [
        (str(labels.get("subtotal", "Subtotal")), _money(float(data["subtotal"]), data)),
        (str(labels.get("discount", "Discount")), _money(float(data["discount"]), data)),
        (str(labels.get("tax", "Tax")), _money(float(data["tax"]), data)),
        (str(labels.get("shipping", "Shipping")), _money(float(data["shipping"]), data)),
        (str(labels.get("paid", "Paid")), _money(float(data["paid"]), data)),
        (str(labels.get("balance_due", "Balance due")), _money(float(data["balance_due"]), data)),
    ]
    row_h = max(3.8, component["height_mm"] / len(rows))
    compact = component["height_mm"] <= 30
    label_size = 5.1 if compact else 5.7
    value_size = 5.35 if compact else 5.9
    label_width = component["width_mm"] * 0.46
    gap = 1.6 if compact else 2.2
    value_x = component["x_mm"] + label_width + gap
    value_width = max(8, component["width_mm"] - label_width - gap)
    y = component["y_mm"]
    for index, (label, value) in enumerate(rows):
        bold = index == len(rows) - 1
        canvas.text(
            component["x_mm"],
            y + 0.5,
            label,
            size=label_size,
            bold=bold,
            color="#64748b",
            width_mm=label_width,
            min_size=4.0,
        )
        canvas.text(
            value_x,
            y + 0.5,
            value,
            size=value_size,
            bold=True,
            align="right",
            width_mm=value_width,
            min_size=4.0,
        )
        y += row_h


def _render_payment(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any]) -> None:
    data = sample["data"]
    labels = data.get("labels", {})
    payment = data["payment"]
    canvas.line(component["x_mm"], component["y_mm"], component["x_mm"] + component["width_mm"], component["y_mm"], color=sample["template"]["secondary"], line_width=0.7)
    if component["height_mm"] <= 8:
        canvas.text(component["x_mm"], component["y_mm"] + 1.0, str(labels.get("payment", "Payment")).upper(), size=5.0, bold=True, color=sample["template"]["accent"])
        canvas.wrapped_text(component["x_mm"], component["y_mm"] + 4.0, component["width_mm"], f'{payment["method"]} {payment["account"]}', size=5.0, max_lines=1)
        return
    if component["height_mm"] <= 14:
        canvas.text(component["x_mm"], component["y_mm"] + 1.4, str(labels.get("payment", "Payment")).upper(), size=5.3, bold=True, color=sample["template"]["accent"])
        canvas.wrapped_text(component["x_mm"], component["y_mm"] + 5.0, component["width_mm"], f'{payment["method"]} {payment["account"]}', size=5.2, max_lines=1)
        canvas.wrapped_text(component["x_mm"], component["y_mm"] + 8.6, component["width_mm"], payment["reference"], size=4.9, max_lines=1)
        return
    canvas.text(component["x_mm"], component["y_mm"] + 2, str(labels.get("payment", "Payment")).upper(), size=5.8, bold=True, color=sample["template"]["accent"])
    canvas.wrapped_text(component["x_mm"], component["y_mm"] + 6, component["width_mm"], f'{payment["method"]} {payment["account"]}', size=5.8, max_lines=1)
    canvas.wrapped_text(component["x_mm"], component["y_mm"] + 10, component["width_mm"], payment["reference"], size=5.4, max_lines=2)
    canvas.wrapped_text(component["x_mm"], component["y_mm"] + 17, component["width_mm"], payment["remit_to"], size=5.4, max_lines=1)


def _render_note(
    canvas: _PdfCanvas,
    component: dict[str, Any],
    sample: dict[str, Any],
    *,
    kind: str,
    title: str,
) -> None:
    data = sample["data"]
    if kind == "footer":
        note_text = data.get("footer_note", "")
    else:
        note_text = data.get("notes", "")
    if not note_text:
        return
    if kind == "footer" and component["height_mm"] <= 12:
        canvas.line(
            component["x_mm"],
            component["y_mm"],
            component["x_mm"] + component["width_mm"],
            component["y_mm"],
            color=sample["template"]["secondary"],
            line_width=0.25,
        )
        canvas.text(
            component["x_mm"],
            component["y_mm"] + 1.2,
            "NOTICE",
            size=4.7,
            bold=True,
            color=sample["template"]["accent"],
        )
        canvas.wrapped_text(
            component["x_mm"],
            component["y_mm"] + 4.0,
            component["width_mm"],
            note_text,
            size=4.7,
            line_height_mm=2.4,
            max_lines=1,
            color="#475569",
        )
        return
    if component["height_mm"] > 12:
        canvas.text(component["x_mm"], component["y_mm"], title.upper(), size=5.5, bold=True, color=sample["template"]["accent"])
        y = component["y_mm"] + 4
    else:
        y = component["y_mm"]
    canvas.wrapped_text(component["x_mm"], y, component["width_mm"], note_text, size=5.2, line_height_mm=2.6, max_lines=3)


def _render_stamp(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any]) -> None:
    canvas.rect(component["x_mm"], component["y_mm"], component["width_mm"], component["height_mm"], stroke=sample["template"]["secondary"], line_width=1.0)
    canvas.text(component["x_mm"], component["y_mm"] + component["height_mm"] / 2 - 2, "APPROVED", size=7, bold=True, color=sample["template"]["secondary"], align="center", width_mm=component["width_mm"])


def _render_barcode(canvas: _PdfCanvas, component: dict[str, Any], sample: dict[str, Any]) -> None:
    x = component["x_mm"]
    for index, width in enumerate([1.2, 0.6, 2.1, 0.8, 1.6, 0.7, 2.4, 0.9, 1.1]):
        canvas.rect(x, component["y_mm"], width, component["height_mm"], fill=sample["template"]["ink"])
        x += width + 0.9


def _column_widths(columns: list[dict[str, Any]], total_width: float) -> list[float]:
    weights = []
    for column in columns:
        key = column.get("key")
        if key in {"item", "item_plain", "description"}:
            weights.append(3.2)
        elif key in {"service_date", "sku", "hsn"}:
            weights.append(1.25)
        elif key in {"quantity", "quantity_unit", "line"}:
            weights.append(0.8)
        else:
            weights.append(1.25)
    total = sum(weights) or 1
    return [total_width * weight / total for weight in weights]


def _table_cell_text(row: dict[str, Any], key: str, data: dict[str, Any]) -> str:
    if key == "item":
        table = data.get("table", {})
        if table.get("show_description") and row.get("description"):
            return f'{row.get("name", "")} - {row.get("description", "")}'
        return str(row.get("name", ""))
    if key == "item_plain":
        return str(row.get("name", ""))
    if key == "line":
        return str(row.get("line", ""))
    if key == "sku":
        return str(row.get("sku", ""))
    if key == "hsn":
        return str(row.get("hsn", ""))
    if key == "service_date":
        return str(row.get("service_date_display", row.get("service_date", "")))
    if key == "quantity":
        return str(row.get("quantity", ""))
    if key == "quantity_unit":
        return str(row.get("quantity_display", row.get("quantity", "")))
    if key == "unit_price":
        return _money(float(row.get("unit_price", 0)), data)
    if key == "amount":
        return _money(float(row.get("amount", 0)), data)
    if key == "taxable":
        return _money(float(row.get("taxable_amount", row.get("amount", 0))), data)
    return str(row.get(key, ""))


def format_invoice_money(value: float, data: dict[str, Any]) -> str:
    return _money(value, data)


def _money(value: float, data: dict[str, Any]) -> str:
    currency = str(data.get("currency", "USD"))
    formatting = data.get("formatting") if isinstance(data.get("formatting"), dict) else {}
    style = str(formatting.get("money_style", "code-prefix-2dp"))
    decimals = int(formatting.get("decimals", 2))
    amount = f"{float(value):,.{decimals}f}" if decimals else f"{float(value):,.0f}"
    if "comma" in style:
        amount = amount.replace(",", "_").replace(".", ",").replace("_", ".")
    if "space" in style:
        amount = amount.replace(",", " ")
    if style.startswith("plain"):
        return amount
    unit = _currency_symbol(currency) if "symbol" in style else currency
    if "suffix" in style:
        return f"{amount} {unit}" if len(unit) > 1 else f"{amount}{unit}"
    separator = "" if len(unit) == 1 else " "
    return f"{unit}{separator}{amount}"


def _currency_symbol(currency: str) -> str:
    return {
        "USD": "$",
        "INR": "Rs",
        "EUR": "€",
        "GBP": "£",
        "AED": "AED",
        "SGD": "S$",
        "CAD": "C$",
        "AUD": "A$",
        "CNY": "¥",
        "JPY": "¥",
        "ZAR": "R",
        "MXN": "Mex$",
    }.get(currency, currency)


def _total_label_column_index(columns: list[dict[str, Any]]) -> int:
    preferred = {"item", "item_plain", "description", "service_date"}
    for index, column in enumerate(columns):
        if column.get("key") in preferred and not column.get("numeric"):
            return index
    for index, column in enumerate(columns):
        if not column.get("numeric"):
            return index
    return 0


def _first_column_index(columns: list[dict[str, Any]], keys: set[str]) -> int | None:
    for index, column in enumerate(columns):
        if column.get("key") in keys:
            return index
    return None


def _last_column_index(columns: list[dict[str, Any]], keys: set[str]) -> int | None:
    for index in range(len(columns) - 1, -1, -1):
        if columns[index].get("key") in keys:
            return index
    return None


def _initials(name: str) -> str:
    parts = [part[0] for part in name.replace("&", " ").split() if part[:1]]
    return "".join(parts[:2]).upper() or "Z"


def _rgb(value: str) -> str:
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return "0 0 0"
    r = int(value[0:2], 16) / 255
    g = int(value[2:4], 16) / 255
    b = int(value[4:6], 16) / 255
    return f"{r:.4f} {g:.4f} {b:.4f}"


def _wrap_text(
    text: str,
    width_mm: float,
    size: float,
    *,
    max_lines: int,
    font: str = "F1",
) -> list[str]:
    box_width = width_mm * MM_TO_PT
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if _text_width(candidate, size, font=font) <= box_width:
            current = candidate
            continue
        if current:
            lines.append(current)
        current = _truncate_to_width(word, size, box_width, font=font)
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and words:
        lines[-1] = _truncate_to_width(lines[-1], size, box_width, font=font)
    return lines or [""]


def _fit_font_size(
    text: str,
    size: float,
    box_width_pt: float,
    *,
    min_size: float,
    font: str = "F1",
) -> float:
    estimated = _text_width(text, size, font=font)
    if estimated <= box_width_pt or estimated <= 0:
        return size
    return max(min_size, size * (box_width_pt / estimated))


def _truncate_to_width(
    text: str,
    size: float,
    box_width_pt: float,
    *,
    font: str = "F1",
) -> str:
    if _text_width(text, size, font=font) <= box_width_pt:
        return text
    ellipsis = "."
    if _text_width(ellipsis, size, font=font) > box_width_pt:
        return ellipsis
    output = ""
    for char in text:
        candidate = f"{output}{char}".rstrip()
        if _text_width(f"{candidate}{ellipsis}", size, font=font) > box_width_pt:
            break
        output = candidate
    return f"{output.rstrip()}{ellipsis}" if output else ellipsis


def _text_width(text: str, size: float, *, font: str = "F1") -> float:
    widths, fallback = FONT_WIDTHS.get(font, FONT_WIDTHS["F1"])
    return sum(widths.get(char, fallback) for char in text) * size / 1000


def _pdf_safe_text(text: str) -> str:
    replacements = {
        "₹": "Rs",
        "د.إ": "AED",
        "–": "-",
        "—": "-",
        "’": "'",
        "“": '"',
        "”": '"',
    }
    value = str(text)
    for source, target in replacements.items():
        value = value.replace(source, target)
    return value.encode("cp1252", errors="replace").decode("cp1252")


def _escape_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_pdf(pages: list[_PdfCanvas]) -> bytes:
    objects: dict[int, bytes] = {}
    objects[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    font_objects = {3: "F1", 4: "F2", 5: "F3", 6: "F4", 7: "F5", 8: "F6"}
    font_resource = "<< /F1 3 0 R /F2 4 0 R /F3 5 0 R /F4 6 0 R /F5 7 0 R /F6 8 0 R >>"
    for object_id, font_key in font_objects.items():
        font_name = FONT_NAMES[font_key]
        objects[object_id] = (
            f"<< /Type /Font /Subtype /Type1 /BaseFont /{font_name} "
            "/Encoding /WinAnsiEncoding >>"
        ).encode("ascii")

    page_ids = []
    next_id = 9
    for page in pages:
        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        stream = page.stream()
        objects[content_id] = b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream"
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page.width_pt:.2f} {page.height_pt:.2f}] "
            f"/Resources << /Font {font_resource} >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_ids.append(page_id)

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    output = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = {0: 0}
    for object_id in sorted(objects):
        offsets[object_id] = len(output)
        output.extend(f"{object_id} 0 obj\n".encode("ascii"))
        output.extend(objects[object_id])
        output.extend(b"\nendobj\n")
    xref_offset = len(output)
    max_id = max(objects)
    output.extend(f"xref\n0 {max_id + 1}\n".encode("ascii"))
    output.extend(b"0000000000 65535 f \n")
    for object_id in range(1, max_id + 1):
        output.extend(f"{offsets[object_id]:010d} 00000 n \n".encode("ascii"))
    output.extend(
        f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    return bytes(output)
