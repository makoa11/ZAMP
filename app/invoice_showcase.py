from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from typing import Any

from .invoice_degradation import DEFAULT_DEGRADATION_PROFILES, degrade_pdf_to_image_pdf
from .invoice_fixtures import generate_invoice_stress_fixtures
from .invoice_generator import (
    AP_EDGE_CASE_SCENARIOS,
    BASE_TEMPLATES,
    CAPTURE_PROFILES,
    PAPER_FORMATS,
    PDF_OCCLUSION_EDGE_CASE_START_INDEX,
    VISUAL_EDGE_CASE_SCENARIOS,
    generate_invoice,
)
from .invoice_pdf import render_invoice_pdf


SHOWCASE_SEED = 7300
FIXTURE_PAGE_COUNTS = {
    "multipage-continuation": 3,
    "ambiguous-labels-mixed-totals": 4,
    "currency-glyphs": 5,
}


@dataclass(frozen=True)
class InvoiceShowcaseDocument:
    slug: str
    title: str
    group: str
    description: str
    page_count: int
    tags: tuple[str, ...]


def _label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").title()


def _clean_documents() -> tuple[InvoiceShowcaseDocument, ...]:
    documents = []
    for template, capture in zip(BASE_TEMPLATES, CAPTURE_PROFILES):
        documents.append(
            InvoiceShowcaseDocument(
                slug=f"clean-{template.slug}",
                title=f"{template.name} · {capture['name']}",
                group="Clean invoice types",
                description=(
                    f"{template.industry} layout using {capture['date_pattern']} dates, "
                    f"{capture['currency']} {capture['money_style']} money, and the "
                    f"{capture['table_variant']} table schema."
                ),
                page_count=1,
                tags=(
                    template.layout_family,
                    str(capture["currency"]),
                    str(capture["date_pattern"]),
                    str(capture["table_variant"]),
                ),
            )
        )
    return tuple(documents)


def _ap_documents() -> tuple[InvoiceShowcaseDocument, ...]:
    descriptions = {
        "split_po_partial_billing": "A partial invoice consumes the remaining balance of a previously used purchase order.",
        "amount_variance_within_tolerance": "The invoice exceeds the PO amount but remains inside the configured tolerance.",
        "amount_variance_above_tolerance": "The invoice exceeds both the PO amount and the configured tolerance.",
        "duplicate_invoice_number_normalized": "Formatting changes hide an invoice number that matches a prior normalized number.",
        "missing_po_implied_match": "The printed PO is missing, while vendor, amount, lines, and service period imply a match.",
        "vendor_bank_detail_changed": "PO and amount match, but remittance details differ from the approved vendor master.",
        "credit_memo_negative_balance": "A credit memo contains negative line items and a negative balance due.",
    }
    return tuple(
        InvoiceShowcaseDocument(
            slug=f"ap-{scenario}",
            title=_label(scenario),
            group="Accounts payable scenarios",
            description=descriptions[scenario],
            page_count=1,
            tags=("AP edge case", _label(scenario)),
        )
        for scenario in AP_EDGE_CASE_SCENARIOS
        if scenario != "none"
    )


def _stress_documents() -> tuple[InvoiceShowcaseDocument, ...]:
    return (
        InvoiceShowcaseDocument(
            slug="stress-table-amount-boundary-collision",
            title="Table amount boundary collision",
            group="Rendering stress scenarios",
            description="Numeric amounts deliberately sit close to or cross table rules.",
            page_count=1,
            tags=("table collision", "numeric drift"),
        ),
        InvoiceShowcaseDocument(
            slug="stress-invoice-number-seal-occlusion",
            title="Invoice number seal occlusion",
            group="Rendering stress scenarios",
            description="A stamp deliberately covers part of the invoice number.",
            page_count=1,
            tags=("stamp", "occluded identifier"),
        ),
        InvoiceShowcaseDocument(
            slug="stress-multipage-continuation",
            title="Multi-page continuation",
            group="Rendering stress scenarios",
            description="One long invoice continues its line-item table across three pages and places totals on the final page.",
            page_count=3,
            tags=("multi-page", "continued table", "dense lines"),
        ),
        InvoiceShowcaseDocument(
            slug="stress-ambiguous-labels-mixed-totals",
            title="Ambiguous labels and mixed totals",
            group="Rendering stress scenarios",
            description="Account, To, Source, and Entity labels combine with table-row and side-panel totals.",
            page_count=4,
            tags=("ambiguous labels", "mixed totals"),
        ),
        InvoiceShowcaseDocument(
            slug="stress-currency-glyphs",
            title="Currency glyphs and separators",
            group="Rendering stress scenarios",
            description="INR, EUR, CNY, JPY, and MXN exercise symbols, codes, zero decimals, and localized separators.",
            page_count=5,
            tags=("5 currencies", "glyphs", "localized separators"),
        ),
    )


def _paper_documents() -> tuple[InvoiceShowcaseDocument, ...]:
    return tuple(
        InvoiceShowcaseDocument(
            slug=f"paper-{paper.slug}",
            title=paper.label,
            group="Page size variants",
            description=f"The same representative invoice reflowed to {paper.width_mm:g} × {paper.height_mm:g} mm.",
            page_count=1,
            tags=(paper.slug, f"{paper.width_mm:g} × {paper.height_mm:g} mm"),
        )
        for paper in PAPER_FORMATS
    )


def _scan_documents() -> tuple[InvoiceShowcaseDocument, ...]:
    return tuple(
        InvoiceShowcaseDocument(
            slug=f"degraded-{profile.name}",
            title=_label(profile.name),
            group="Scan and OCR stress",
            description=(
                f"Image-only PDF at {profile.dpi} DPI with rotation {profile.rotation}°, "
                f"skew {profile.skew_degrees:g}°, blur {profile.blur_sigma:g}, "
                f"contrast {profile.contrast:g}, noise {profile.noise_stddev:g}, and JPEG quality {profile.jpeg_quality}."
            ),
            page_count=1,
            tags=("image-only", f"{profile.dpi} DPI", f"JPEG {profile.jpeg_quality}"),
        )
        for profile in DEFAULT_DEGRADATION_PROFILES
    )


SHOWCASE_DOCUMENTS: tuple[InvoiceShowcaseDocument, ...] = (
    _clean_documents()
    + _ap_documents()
    + _stress_documents()
    + _paper_documents()
    + _scan_documents()
)


def showcase_document(slug: str) -> InvoiceShowcaseDocument | None:
    return next((document for document in SHOWCASE_DOCUMENTS if document.slug == slug), None)


def showcase_samples(slug: str, *, today: date | None = None) -> list[dict[str, Any]]:
    if showcase_document(slug) is None:
        raise ValueError(f"Unknown showcase document: {slug}")

    invoice_date = today or date.today()
    if slug.startswith("clean-"):
        template_slug = slug.removeprefix("clean-")
        template_index = next(
            index for index, template in enumerate(BASE_TEMPLATES) if template.slug == template_slug
        )
        return [
            generate_invoice(
                template_slug=template_slug,
                paper_slug="a4",
                seed=SHOWCASE_SEED + (template_index * 97),
                variation_index=0,
                today=invoice_date,
            )
        ]

    if slug.startswith("ap-"):
        scenario = slug.removeprefix("ap-")
        scenario_index = AP_EDGE_CASE_SCENARIOS.index(scenario)
        variation_index = next(
            candidate
            for candidate in range(scenario_index, scenario_index + 24)
            if candidate % len(AP_EDGE_CASE_SCENARIOS) == scenario_index
            and candidate % len(VISUAL_EDGE_CASE_SCENARIOS) == 0
        )
        return [
            generate_invoice(
                template_slug=BASE_TEMPLATES[(scenario_index - 1) % len(BASE_TEMPLATES)].slug,
                paper_slug="a4",
                seed=SHOWCASE_SEED + 1000 + (variation_index * 97),
                variation_index=variation_index,
                today=invoice_date,
            )
        ]

    if slug == "stress-table-amount-boundary-collision":
        variation_index = next(
            candidate
            for candidate in range(len(AP_EDGE_CASE_SCENARIOS) * len(VISUAL_EDGE_CASE_SCENARIOS))
            if candidate % len(AP_EDGE_CASE_SCENARIOS) == 0
            and candidate % len(VISUAL_EDGE_CASE_SCENARIOS) == 1
        )
        return [
            generate_invoice(
                template_slug="ledger-clean",
                paper_slug="a4",
                seed=SHOWCASE_SEED + 2001,
                variation_index=variation_index,
                today=invoice_date,
            )
        ]
    if slug == "stress-invoice-number-seal-occlusion":
        variation_index = next(
            candidate
            for candidate in range(
                PDF_OCCLUSION_EDGE_CASE_START_INDEX,
                PDF_OCCLUSION_EDGE_CASE_START_INDEX
                + len(AP_EDGE_CASE_SCENARIOS) * len(VISUAL_EDGE_CASE_SCENARIOS),
            )
            if candidate % len(AP_EDGE_CASE_SCENARIOS) == 0
            and candidate % len(VISUAL_EDGE_CASE_SCENARIOS) == 2
        )
        return [
            generate_invoice(
                template_slug="ledger-clean",
                paper_slug="a4",
                seed=SHOWCASE_SEED + 2017,
                variation_index=variation_index,
                today=invoice_date,
            )
        ]

    fixture_slug = slug.removeprefix("stress-")
    if fixture_slug in FIXTURE_PAGE_COUNTS:
        fixture = next(
            candidate
            for candidate in generate_invoice_stress_fixtures(
                seed=SHOWCASE_SEED + 3000,
                today=invoice_date,
            )
            if candidate.slug == fixture_slug
        )
        return fixture.samples

    if slug.startswith("paper-"):
        paper_slug = slug.removeprefix("paper-")
        return [
            generate_invoice(
                template_slug="ledger-clean",
                paper_slug=paper_slug,
                seed=SHOWCASE_SEED + 4000,
                variation_index=0,
                today=invoice_date,
            )
        ]

    if slug.startswith("degraded-"):
        return [
            generate_invoice(
                template_slug="ledger-clean",
                paper_slug="a4",
                seed=SHOWCASE_SEED + 5000,
                variation_index=0,
                today=invoice_date,
            )
        ]

    raise ValueError(f"Unknown showcase document: {slug}")


@lru_cache(maxsize=len(SHOWCASE_DOCUMENTS))
def render_showcase_pdf(slug: str) -> bytes:
    samples = showcase_samples(slug)
    content = render_invoice_pdf(samples)
    if not slug.startswith("degraded-"):
        return content

    profile_name = slug.removeprefix("degraded-")
    profile_index, profile = next(
        (index, candidate)
        for index, candidate in enumerate(DEFAULT_DEGRADATION_PROFILES)
        if candidate.name == profile_name
    )
    return degrade_pdf_to_image_pdf(
        content,
        profile=profile,
        seed=SHOWCASE_SEED + 6000 + profile_index,
    )


@lru_cache(maxsize=128)
def render_showcase_page_png(slug: str, page_number: int) -> bytes:
    document = showcase_document(slug)
    if document is None:
        raise ValueError(f"Unknown showcase document: {slug}")
    if page_number < 1 or page_number > document.page_count:
        raise ValueError(f"Showcase page must be between 1 and {document.page_count}.")

    try:
        import fitz  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError("Install PyMuPDF to render showcase previews.") from exc

    source = fitz.open(stream=render_showcase_pdf(slug), filetype="pdf")
    try:
        page = source.load_page(page_number - 1)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(1.6, 1.6), alpha=False)
        return pixmap.tobytes("png")
    finally:
        source.close()
