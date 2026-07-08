from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

from .invoice_generator import generate_invoice
from .invoice_pdf import format_invoice_money


MONEY_QUANT = Decimal("0.01")
AMBIGUOUS_ENTITY_LABELS = {"Account", "To", "Source", "Entity"}
GLYPH_SENSITIVE_CURRENCIES = {"INR", "EUR", "CNY", "JPY", "MXN", "AUD", "CAD", "SGD"}


@dataclass(frozen=True)
class InvoicePdfFixture:
    slug: str
    filename: str
    samples: list[dict[str, Any]]
    description: str
    tags: tuple[str, ...]


def write_invoice_manifest(
    pdf_path: Path,
    samples: list[dict[str, Any]],
    *,
    suite: str = "standard",
    fixture_slug: str | None = None,
) -> Path:
    manifest = build_invoice_manifest(
        samples,
        pdf_filename=pdf_path.name,
        suite=suite,
        fixture_slug=fixture_slug,
    )
    manifest_path = pdf_path.with_suffix(".manifest.json")
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def build_invoice_manifest(
    samples: list[dict[str, Any]],
    *,
    pdf_filename: str,
    suite: str = "standard",
    fixture_slug: str | None = None,
) -> dict[str, Any]:
    groups: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for pdf_page_number, sample in enumerate(samples, start=1):
        fixture = _fixture_meta(sample)
        document_id = str(fixture.get("document_id") or sample["id"])
        groups.setdefault(document_id, []).append((pdf_page_number, sample))

    documents = [
        _manifest_document(document_id, pages)
        for document_id, pages in groups.items()
    ]
    challenge_tags = sorted(
        {
            tag
            for document in documents
            for tag in document["challenge_tags"]
        }
    )
    return {
        "schema_version": 1,
        "suite": suite,
        "fixture_slug": fixture_slug,
        "pdf": {
            "filename": pdf_filename,
            "page_count": len(samples),
        },
        "challenge_tags": challenge_tags,
        "documents": documents,
    }


def generate_invoice_stress_fixtures(
    *,
    seed: int = 1000,
    today: date | None = None,
) -> list[InvoicePdfFixture]:
    invoice_date = today or date.today()
    return [
        _multipage_continuation_fixture(seed=seed + 101, today=invoice_date),
        _ambiguous_labels_mixed_totals_fixture(seed=seed + 202, today=invoice_date),
        _currency_glyph_fixture(seed=seed + 303, today=invoice_date),
    ]


def _manifest_document(
    document_id: str,
    pages: list[tuple[int, dict[str, Any]]],
) -> dict[str, Any]:
    sorted_pages = sorted(
        pages,
        key=lambda item: int(_fixture_meta(item[1]).get("page_index") or item[0]),
    )
    first_sample = sorted_pages[0][1]
    data = first_sample["data"]
    items = _combined_line_items([sample for _, sample in sorted_pages])
    pages_manifest = [_manifest_page(pdf_page_number, sample) for pdf_page_number, sample in sorted_pages]
    total_placement = _document_total_placement([sample for _, sample in sorted_pages])
    challenge_tags = _document_challenge_tags(
        data=data,
        pages=pages_manifest,
        samples=[sample for _, sample in sorted_pages],
        total_placement=total_placement,
    )
    return {
        "document_id": document_id,
        "sample_ids": [sample["id"] for _, sample in sorted_pages],
        "invoice_number": data["invoice_number"],
        "purchase_order": data["purchase_order"],
        "status": data["status"],
        "seller": _entity_manifest(data["seller"]),
        "buyer": _entity_manifest(data["buyer"]),
        "issue_date": {
            "value": data["issue_date"],
            "display": data.get("issue_date_display", data["issue_date"]),
        },
        "due_date": {
            "value": data["due_date"],
            "display": data.get("due_date_display", data["due_date"]),
        },
        "terms": data["terms"],
        "currency": data["currency"],
        "labels": dict(data.get("labels", {})),
        "amounts": _amount_manifest(data),
        "line_items": [_line_item_manifest(item, data) for item in items],
        "table": {
            "variant": data.get("table", {}).get("variant"),
            "columns": [
                {
                    "key": column.get("key"),
                    "label": column.get("label"),
                    "numeric": bool(column.get("numeric")),
                }
                for column in data.get("table", {}).get("columns", [])
            ],
            "show_description": bool(data.get("table", {}).get("show_description", True)),
            "total_placement": total_placement,
            "continued_across_pages": len(sorted_pages) > 1,
        },
        "pages": pages_manifest,
        "challenge_tags": challenge_tags,
    }


def _manifest_page(pdf_page_number: int, sample: dict[str, Any]) -> dict[str, Any]:
    fixture = _fixture_meta(sample)
    data = sample["data"]
    line_numbers = [int(item.get("line", index)) for index, item in enumerate(data["items"], start=1)]
    return {
        "pdf_page_number": pdf_page_number,
        "document_page_number": int(fixture.get("page_index") or 1),
        "document_page_count": int(fixture.get("page_count") or 1),
        "sample_id": sample["id"],
        "line_item_numbers": line_numbers,
        "table_continues_from_previous_page": bool(fixture.get("table_continues_from_previous_page")),
        "table_continues_after_page": bool(fixture.get("table_continues_after_page")),
        "renders_totals": _page_renders_totals(sample),
        "total_placement": _page_total_placement(sample),
        "notes_near_table_bounds": _has_notes_near_table(sample),
        "components": _component_manifest(sample),
    }


def _combined_line_items(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items_by_line: dict[int, dict[str, Any]] = {}
    fallback_line = 1
    for sample in samples:
        for item in sample["data"].get("items", []):
            try:
                line = int(item.get("line", fallback_line))
            except (TypeError, ValueError):
                line = fallback_line
            items_by_line[line] = item
            fallback_line += 1
    return [items_by_line[line] for line in sorted(items_by_line)]


def _document_total_placement(samples: list[dict[str, Any]]) -> str:
    placements = {
        placement
        for sample in samples
        for placement in [_page_total_placement(sample)]
        if placement != "none"
    }
    if len(placements) > 1:
        return "mixed"
    return next(iter(placements), "none")


def _page_total_placement(sample: dict[str, Any]) -> str:
    has_table_total = bool(sample["data"].get("table", {}).get("total_in_table"))
    has_side_panel = any(component["kind"] == "totals" for component in sample.get("components", []))
    if has_table_total and has_side_panel:
        return "mixed"
    if has_table_total:
        return "table_row"
    if has_side_panel:
        return "side_panel"
    return "none"


def _page_renders_totals(sample: dict[str, Any]) -> bool:
    return _page_total_placement(sample) != "none"


def _document_challenge_tags(
    *,
    data: dict[str, Any],
    pages: list[dict[str, Any]],
    samples: list[dict[str, Any]],
    total_placement: str,
) -> list[str]:
    tags = {
        tag
        for sample in samples
        for tag in _fixture_meta(sample).get("challenge_tags", [])
        if isinstance(tag, str)
    }
    if len(pages) > 1:
        tags.add("multi_page_invoice")
    if any(page["table_continues_from_previous_page"] or page["table_continues_after_page"] for page in pages):
        tags.add("table_continuation_across_pages")
    if any(page["notes_near_table_bounds"] for page in pages):
        tags.add("notes_near_table_bounds")
    if total_placement == "table_row":
        tags.add("totals_in_table_row")
    elif total_placement == "side_panel":
        tags.add("totals_in_side_panel")
    elif total_placement == "mixed":
        tags.add("mixed_total_positions")
    labels = data.get("labels", {})
    if any(str(labels.get(key, "")) in AMBIGUOUS_ENTITY_LABELS for key in ("seller", "buyer", "document_title")):
        tags.add("ambiguous_entity_labels")
    if str(data.get("currency")) in GLYPH_SENSITIVE_CURRENCIES:
        tags.add("messy_currency_glyphs")
    if _uses_localized_money_separator(data):
        tags.add("localized_decimal_separator")
    return sorted(tags)


def _entity_manifest(entity: dict[str, Any]) -> dict[str, Any]:
    keys = ("name", "line1", "city", "email", "tax_id")
    return {key: entity[key] for key in keys if key in entity}


def _amount_manifest(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        key: {
            "value": _normalized_money(data[key]),
            "visible_value": _visible_money_value(data[key], data),
            "display": format_invoice_money(float(data[key]), data),
        }
        for key in ("subtotal", "discount", "tax", "shipping", "paid", "total", "balance_due")
    }


def _line_item_manifest(item: dict[str, Any], data: dict[str, Any]) -> dict[str, Any]:
    return {
        "line": item.get("line"),
        "sku": item.get("sku"),
        "hsn": item.get("hsn"),
        "name": item.get("name"),
        "description": item.get("description"),
        "quantity": item.get("quantity"),
        "quantity_display": item.get("quantity_display"),
        "unit_price": {
            "value": _normalized_money(item.get("unit_price", 0)),
            "visible_value": _visible_money_value(item.get("unit_price", 0), data),
            "display": format_invoice_money(float(item.get("unit_price", 0)), data),
        },
        "amount": {
            "value": _normalized_money(item.get("amount", 0)),
            "visible_value": _visible_money_value(item.get("amount", 0), data),
            "display": format_invoice_money(float(item.get("amount", 0)), data),
        },
        "service_date": {
            "value": item.get("service_date"),
            "display": item.get("service_date_display", item.get("service_date")),
        },
    }


def _component_manifest(sample: dict[str, Any]) -> list[dict[str, Any]]:
    interesting = {
        "seller",
        "buyer",
        "invoice-meta",
        "dates",
        "items-table",
        "totals",
        "payment",
        "terms",
        "footer",
        "remittance",
    }
    return [
        {
            "kind": component["kind"],
            "bbox_mm": [
                component["x_mm"],
                component["y_mm"],
                component["width_mm"],
                component["height_mm"],
            ],
        }
        for component in sample.get("components", [])
        if component["kind"] in interesting
    ]


def _has_notes_near_table(sample: dict[str, Any]) -> bool:
    table = next((component for component in sample.get("components", []) if component["kind"] == "items-table"), None)
    if not table:
        return False
    table_bottom = float(table["y_mm"]) + float(table["height_mm"])
    for component in sample.get("components", []):
        if component["kind"] not in {"terms", "footer", "remittance"}:
            continue
        vertical_gap = float(component["y_mm"]) - table_bottom
        if 0 <= vertical_gap <= 8:
            return True
    return False


def _fixture_meta(sample: dict[str, Any]) -> dict[str, Any]:
    fixture = sample.get("fixture")
    return fixture if isinstance(fixture, dict) else {}


def _uses_localized_money_separator(data: dict[str, Any]) -> bool:
    formatting = data.get("formatting") if isinstance(data.get("formatting"), dict) else {}
    return "comma" in str(formatting.get("money_style", ""))


def _normalized_money(value: Any) -> str:
    return str(_round_money(value))


def _visible_money_value(value: Any, data: dict[str, Any]) -> str:
    formatting = data.get("formatting") if isinstance(data.get("formatting"), dict) else {}
    decimals = int(formatting.get("decimals", 2))
    quant = Decimal("1") if decimals <= 0 else Decimal(f"0.{'0' * (decimals - 1)}1")
    return str(Decimal(str(value)).quantize(quant, rounding=ROUND_HALF_UP))


def _round_money(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _multipage_continuation_fixture(*, seed: int, today: date) -> InvoicePdfFixture:
    base = generate_invoice(
        template_slug="ledger-clean",
        paper_slug="a4",
        seed=seed,
        variation_index=0,
        today=today,
    )
    base = _sample_with_expanded_items(base, target_count=24, seed=seed)
    base["data"]["table"]["total_in_table"] = False
    base["data"]["notes"] = "Line items continue across pages; note text is intentionally close to the table boundary."
    base["data"]["footer_note"] = "Footer placed near table bounds for extraction tests."

    groups = [
        base["data"]["items"][:9],
        base["data"]["items"][9:18],
        base["data"]["items"][18:],
    ]
    samples = []
    for index, items in enumerate(groups, start=1):
        page = copy.deepcopy(base)
        page["id"] = f"{base['id']}-page-{index}"
        page["data"]["items"] = items
        page["data"]["table"]["total_in_table"] = False
        page["data"]["footer_note"] = f"Page {index} of {len(groups)} - {base['data']['invoice_number']}"
        if index > 1:
            page["data"]["labels"] = dict(page["data"]["labels"])
            page["data"]["labels"]["document_title"] = "Invoice Continued"
            page["data"]["notes"] = "Continued table rows from the previous page."
        page["components"] = _multipage_components(page, page_index=index, page_count=len(groups))
        page["fixture"] = {
            "document_id": base["id"],
            "page_index": index,
            "page_count": len(groups),
            "table_continues_from_previous_page": index > 1,
            "table_continues_after_page": index < len(groups),
            "challenge_tags": [
                "multi_page_invoice",
                "table_continuation_across_pages",
                "notes_near_table_bounds",
                "totals_in_side_panel",
            ],
        }
        page["layout_score"] = _layout_score(page)
        samples.append(page)

    return InvoicePdfFixture(
        slug="multipage-continuation",
        filename="invoice-stress-multipage-continuation.pdf",
        samples=samples,
        description="One invoice split across three pages with line-item continuation and notes close to table bounds.",
        tags=(
            "multi_page_invoice",
            "table_continuation_across_pages",
            "notes_near_table_bounds",
            "totals_in_side_panel",
        ),
    )


def _ambiguous_labels_mixed_totals_fixture(*, seed: int, today: date) -> InvoicePdfFixture:
    configs = [
        ("apex-grid", {"buyer": "Account"}, "side_panel", "Account label with side-panel totals"),
        ("civic-classic", {"buyer": "To"}, "table_row", "To label with total row"),
        ("ribbon-pro", {"seller": "Entity"}, "table_row", "Entity label with total row"),
        ("mono-archive", {"seller": "Source"}, "table_row", "Source label with total row"),
    ]
    samples = []
    for index, (template_slug, label_overrides, total_placement, note) in enumerate(configs):
        sample = generate_invoice(
            template_slug=template_slug,
            paper_slug="a4",
            seed=seed + (index * 37),
            variation_index=0,
            today=today,
        )
        _force_entity_labels(sample, label_overrides)
        _force_total_placement(sample, total_placement)
        sample["data"]["notes"] = note
        sample["fixture"] = {
            "document_id": sample["id"],
            "page_index": 1,
            "page_count": 1,
            "challenge_tags": [
                "ambiguous_entity_labels",
                "mixed_total_positions",
                _page_total_tag(sample),
            ],
        }
        samples.append(sample)
    return InvoicePdfFixture(
        slug="ambiguous-labels-mixed-totals",
        filename="invoice-stress-ambiguous-labels-mixed-totals.pdf",
        samples=samples,
        description="Four invoices with ambiguous entity labels and both side-panel and table-row total placement.",
        tags=("ambiguous_entity_labels", "mixed_total_positions"),
    )


def _currency_glyph_fixture(*, seed: int, today: date) -> InvoicePdfFixture:
    configs = [
        ("north-star", "INR", "symbol-prefix-0dp", 0),
        ("harbor-rail", "EUR", "symbol-prefix-comma-2dp", 2),
        ("ribbon-pro", "CNY", "symbol-prefix-0dp", 0),
        ("mono-archive", "JPY", "symbol-prefix-0dp", 0),
        ("signal-card", "MXN", "symbol-prefix-comma-2dp", 2),
    ]
    samples = []
    for index, (template_slug, currency, money_style, decimals) in enumerate(configs):
        sample = generate_invoice(
            template_slug=template_slug,
            paper_slug="a4",
            seed=seed + (index * 41),
            variation_index=0,
            today=today,
        )
        _force_money_format(
            sample,
            currency=currency,
            money_style=money_style,
            decimals=decimals,
        )
        sample["data"]["notes"] = "Currency rendering intentionally covers symbol, code, zero-decimal, and localized separator cases."
        sample["fixture"] = {
            "document_id": sample["id"],
            "page_index": 1,
            "page_count": 1,
            "challenge_tags": [
                "messy_currency_glyphs",
                _page_total_tag(sample),
            ],
        }
        if _uses_localized_money_separator(sample["data"]):
            sample["fixture"]["challenge_tags"].append("localized_decimal_separator")
        samples.append(sample)
    return InvoicePdfFixture(
        slug="currency-glyphs",
        filename="invoice-stress-currency-glyphs.pdf",
        samples=samples,
        description="Invoices with glyph-sensitive and confusable currencies such as Rs, EUR, CNY, JPY, and Mex$.",
        tags=("messy_currency_glyphs", "localized_decimal_separator"),
    )


def _force_entity_labels(sample: dict[str, Any], overrides: dict[str, str]) -> None:
    labels = dict(sample["data"].get("labels", {}))
    labels.update(overrides)
    sample["data"]["labels"] = labels


def _force_money_format(
    sample: dict[str, Any],
    *,
    currency: str,
    money_style: str,
    decimals: int,
) -> None:
    formatting = dict(sample["data"].get("formatting", {}))
    formatting["money_style"] = money_style
    formatting["decimals"] = decimals
    sample["data"]["currency"] = currency
    sample["data"]["formatting"] = formatting
    sample["data"]["capture_profile"] = f"stress-{currency.lower()}-{money_style}"


def _force_total_placement(sample: dict[str, Any], placement: str) -> None:
    table = dict(sample["data"].get("table", {}))
    table["total_in_table"] = placement == "table_row"
    sample["data"]["table"] = table
    if placement == "table_row":
        sample["components"] = [
            component
            for component in sample.get("components", [])
            if component["kind"] != "totals"
        ]
        sample["layout_score"] = _layout_score(sample)
        return
    if placement != "side_panel":
        sample["layout_score"] = _layout_score(sample)
        return
    if not any(component["kind"] == "totals" for component in sample.get("components", [])):
        sample["components"].append(_default_totals_component(sample))
    sample["layout_score"] = _layout_score(sample)


def _default_totals_component(sample: dict[str, Any]) -> dict[str, Any]:
    table = next(
        (component for component in sample.get("components", []) if component["kind"] == "items-table"),
        None,
    )
    if table:
        y = min(float(sample["paper"]["height_mm"]) - 56, float(table["y_mm"]) + float(table["height_mm"]) + 10)
    else:
        y = 228
    return _component("totals", 118, y, 78, 38, priority=1)


def _sample_with_expanded_items(
    sample: dict[str, Any],
    *,
    target_count: int,
    seed: int,
) -> dict[str, Any]:
    expanded_sample = copy.deepcopy(sample)
    data = expanded_sample["data"]
    source_items = list(data["items"])
    issue_date = date.fromisoformat(data["issue_date"])
    units = ["ea", "hrs", "pcs", "days", "sets", "kg"]
    expanded_items = []
    for index in range(target_count):
        source = copy.deepcopy(source_items[index % len(source_items)])
        quantity = [1, 2, 3, 4, 6, 8][(seed + index) % 6]
        multiplier = Decimal(94 + ((seed + index * 7) % 15)) / Decimal("100")
        unit_price = _round_money(Decimal(str(source["unit_price"])) * multiplier)
        amount = _round_money(unit_price * quantity)
        service_date = issue_date - timedelta(days=(index * 3) % 18)
        source["line"] = index + 1
        source["sku"] = f"{str(source.get('sku', 'LN-000')).split('-', 1)[0]}-{seed % 90 + index + 100}"
        source["name"] = _expanded_item_name(str(source.get("name", "Service")), index)
        source["quantity"] = quantity
        source["quantity_display"] = f"{quantity} {units[(seed + index) % len(units)]}"
        source["unit_price"] = float(unit_price)
        source["amount"] = float(amount)
        source["taxable_amount"] = float(amount)
        source["service_date"] = service_date.isoformat()
        source["service_date_display"] = _format_date(service_date, str(data["formatting"]["date_pattern"]))
        expanded_items.append(source)
    data["items"] = expanded_items
    _recalculate_totals(data, discount_rate=Decimal("0.025"), paid_rate=Decimal("0.25"))
    return expanded_sample


def _recalculate_totals(
    data: dict[str, Any],
    *,
    discount_rate: Decimal,
    paid_rate: Decimal,
) -> None:
    subtotal = _round_money(sum(Decimal(str(item["amount"])) for item in data["items"]))
    discount = _round_money(subtotal * discount_rate)
    taxable = _round_money(subtotal - discount)
    tax_rate = Decimal(str(data.get("tax_rate", 0)))
    tax = _round_money(taxable * tax_rate)
    shipping = _round_money(data.get("shipping", 0))
    total = _round_money(taxable + tax + shipping)
    paid = _round_money(total * paid_rate)
    balance_due = _round_money(total - paid)
    data["subtotal"] = float(subtotal)
    data["discount"] = float(discount)
    data["tax"] = float(tax)
    data["shipping"] = float(shipping)
    data["total"] = float(total)
    data["paid"] = float(paid)
    data["balance_due"] = float(balance_due)
    data["total_quantity"] = sum(int(item["quantity"]) for item in data["items"])


def _expanded_item_name(name: str, index: int) -> str:
    if index < 12:
        return name
    return f"{name} phase {(index // 6) + 1}"


def _format_date(value: date, pattern: str) -> str:
    formats = {
        "DDMMYYYY": "%d%m%Y",
        "DDMMYY": "%d%m%y",
        "MMDDYYYY": "%m%d%Y",
        "DD/MM/YYYY": "%d/%m/%Y",
        "MM/DD/YYYY": "%m/%d/%Y",
        "DD-MM-YYYY": "%d-%m-%Y",
        "DD-MM-YY": "%d-%m-%y",
        "YYYYMMDD": "%Y%m%d",
        "YYYY.MM.DD": "%Y.%m.%d",
        "DD Mon YYYY": "%d %b %Y",
        "Mon DD YYYY": "%b %d %Y",
    }
    return value.strftime(formats.get(pattern, "%Y-%m-%d"))


def _multipage_components(
    sample: dict[str, Any],
    *,
    page_index: int,
    page_count: int,
) -> list[dict[str, Any]]:
    header_variant = _header_variant(sample)
    if page_index == 1:
        return [
            _component("company-header", 0, 0, 210, 11, priority=1, variant=header_variant),
            _component("logo", 14, 18, 24, 20, priority=1),
            _component("title", 106, 18, 90, 20, priority=1),
            _component("seller", 14, 46, 72, 31, priority=2),
            _component("invoice-meta", 126, 46, 70, 31, priority=2),
            _component("buyer", 14, 84, 86, 30, priority=2),
            _component("dates", 112, 84, 84, 30, priority=2),
            _component("items-table", 14, 124, 182, 94, priority=1),
            _component("terms", 14, 220, 182, 12, priority=6, optional=True),
            _component("footer", 14, 236, 182, 8, priority=8, optional=True),
        ]
    if page_index < page_count:
        return [
            _component("company-header", 0, 0, 210, 11, priority=1, variant=header_variant),
            _component("title", 14, 20, 95, 16, priority=1),
            _component("invoice-meta", 122, 20, 74, 24, priority=2),
            _component("dates", 122, 50, 74, 24, priority=2),
            _component("items-table", 14, 78, 182, 122, priority=1),
            _component("terms", 14, 202, 182, 12, priority=6, optional=True),
            _component("footer", 14, 218, 182, 8, priority=8, optional=True),
        ]
    return [
        _component("company-header", 0, 0, 210, 11, priority=1, variant=header_variant),
        _component("title", 14, 20, 95, 16, priority=1),
        _component("invoice-meta", 122, 20, 74, 24, priority=2),
        _component("dates", 122, 50, 74, 24, priority=2),
        _component("items-table", 14, 78, 182, 84, priority=1),
        _component("payment", 14, 174, 74, 28, priority=4, optional=True),
        _component("totals", 118, 172, 78, 38, priority=1),
        _component("terms", 14, 222, 182, 13, priority=6, optional=True),
        _component("footer", 14, 240, 182, 8, priority=8, optional=True),
    ]


def _header_variant(sample: dict[str, Any]) -> str:
    header = next(
        (component for component in sample.get("components", []) if component["kind"] == "company-header"),
        None,
    )
    return str((header or {}).get("variant") or "split")


def _component(
    kind: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    priority: int,
    optional: bool = False,
    variant: str | None = None,
) -> dict[str, Any]:
    component = {
        "kind": kind,
        "x_mm": round(x, 2),
        "y_mm": round(y, 2),
        "width_mm": round(width, 2),
        "height_mm": round(height, 2),
        "priority": priority,
        "optional": optional,
    }
    if variant:
        component["variant"] = variant
    return component


def _layout_score(sample: dict[str, Any]) -> dict[str, Any]:
    paper = sample["paper"]
    components = sample["components"]
    area = float(paper["width_mm"]) * float(paper["height_mm"])
    used_area = sum(float(component["width_mm"]) * float(component["height_mm"]) for component in components)
    bottom = max(float(component["y_mm"]) + float(component["height_mm"]) for component in components)
    required = {
        "company-header",
        "logo",
        "title",
        "invoice-meta",
        "buyer",
        "dates",
        "items-table",
        "totals",
    }
    present = {component["kind"] for component in components}
    return {
        "density": round(used_area / area, 3) if area else 0,
        "bottom_mm": round(bottom, 2),
        "overflow_mm": round(max(0.0, bottom - float(paper["height_mm"])), 2),
        "required_components_present": sorted(required.intersection(present)),
        "line_item_count": len(sample["data"]["items"]),
    }


def _page_total_tag(sample: dict[str, Any]) -> str:
    placement = _page_total_placement(sample)
    if placement == "table_row":
        return "totals_in_table_row"
    if placement == "side_panel":
        return "totals_in_side_panel"
    if placement == "mixed":
        return "mixed_total_positions"
    return "totals_not_rendered"
