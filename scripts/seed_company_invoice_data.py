from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.generate_test_pdfs import (  # noqa: E402
    DEFAULT_PDF_COUNT,
    DEFAULT_SEED,
    InvoiceCorpusEntry,
    iter_invoice_corpus,
)
from app.invoice_fixtures import build_invoice_manifest  # noqa: E402
from app.invoice_generator import BASE_TEMPLATES, CAPTURE_PROFILES, PAPER_FORMATS  # noqa: E402


TABLE_NAME = "test_company_invoice_truth"
LABELS_TABLE_NAME = "test_company_invoice_truth_labels"
AMOUNTS_TABLE_NAME = "test_company_invoice_truth_amounts"
LINE_ITEMS_TABLE_NAME = "test_company_invoice_truth_line_items"
TABLE_METADATA_TABLE_NAME = "test_company_invoice_truth_tables"
TABLE_COLUMNS_TABLE_NAME = "test_company_invoice_truth_table_columns"
PAGES_TABLE_NAME = "test_company_invoice_truth_pages"
PAGE_LINE_ITEMS_TABLE_NAME = "test_company_invoice_truth_page_line_items"
PAGE_COMPONENTS_TABLE_NAME = "test_company_invoice_truth_page_components"
SAMPLE_IDS_TABLE_NAME = "test_company_invoice_truth_sample_ids"
CHALLENGE_TAGS_TABLE_NAME = "test_company_invoice_truth_challenge_tags"
SUMMARY_VIEW_NAME = "test_company_invoice_truth_summary"
DEFAULT_MANIFEST_DIR = Path("storage/test_pdfs")
MONEY_QUANT = Decimal("0.01")
JSON_COLUMNS = {"labels", "line_items", "raw_document", "generation_params"}
MONEY_KEYS = ("subtotal", "discount", "tax", "shipping", "paid", "total", "balance_due")

SCHEMA_SQL = (
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        id BIGSERIAL PRIMARY KEY,
        batch_id TEXT NOT NULL,
        pdf_filename TEXT NOT NULL,
        pdf_page_count INTEGER,
        manifest_filename TEXT,
        manifest_schema_version INTEGER,
        document_id TEXT NOT NULL,
        suite TEXT NOT NULL,
        fixture_slug TEXT,
        template_slug TEXT NOT NULL,
        paper_slug TEXT NOT NULL,
        invoice_number TEXT NOT NULL,
        invoice_number_style TEXT NOT NULL,
        vendor_name TEXT NOT NULL,
        vendor_line1 TEXT,
        vendor_city TEXT,
        vendor_email TEXT,
        vendor_tax_id TEXT,
        customer_name TEXT NOT NULL,
        customer_line1 TEXT,
        customer_city TEXT,
        issue_date DATE NOT NULL,
        issue_date_display TEXT,
        due_date DATE NOT NULL,
        due_date_display TEXT,
        purchase_order TEXT,
        terms TEXT,
        status TEXT,
        currency TEXT NOT NULL,
        subtotal NUMERIC(12,2) NOT NULL,
        discount NUMERIC(12,2) NOT NULL,
        tax NUMERIC(12,2) NOT NULL,
        shipping NUMERIC(12,2) NOT NULL,
        paid NUMERIC(12,2) NOT NULL,
        total NUMERIC(12,2) NOT NULL,
        balance_due NUMERIC(12,2) NOT NULL,
        labels JSONB NOT NULL,
        line_items JSONB NOT NULL,
        raw_document JSONB NOT NULL,
        generation_params JSONB NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE (batch_id, document_id),
        UNIQUE (batch_id, vendor_name, invoice_number)
    )
    """,
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS pdf_page_count INTEGER",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS manifest_filename TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS manifest_schema_version INTEGER",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS vendor_line1 TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS vendor_city TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS customer_line1 TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS customer_city TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS issue_date_display TEXT",
    f"ALTER TABLE {TABLE_NAME} ADD COLUMN IF NOT EXISTS due_date_display TEXT",
    f"""
    CREATE TABLE IF NOT EXISTS {LABELS_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        label_key TEXT NOT NULL,
        label_text TEXT NOT NULL,
        PRIMARY KEY (batch_id, document_id, label_key),
        FOREIGN KEY (batch_id, document_id)
            REFERENCES {TABLE_NAME} (batch_id, document_id) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {AMOUNTS_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        amount_key TEXT NOT NULL,
        amount_value NUMERIC(12,2) NOT NULL,
        visible_value TEXT,
        display_value TEXT,
        PRIMARY KEY (batch_id, document_id, amount_key),
        FOREIGN KEY (batch_id, document_id)
            REFERENCES {TABLE_NAME} (batch_id, document_id) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {LINE_ITEMS_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        line_item_index INTEGER NOT NULL,
        line_number INTEGER,
        sku TEXT,
        hsn TEXT,
        item_name TEXT,
        description TEXT,
        quantity NUMERIC(12,4),
        quantity_display TEXT,
        unit_price_value NUMERIC(12,2),
        unit_price_visible_value TEXT,
        unit_price_display TEXT,
        amount_value NUMERIC(12,2),
        amount_visible_value TEXT,
        amount_display TEXT,
        service_date DATE,
        service_date_display TEXT,
        PRIMARY KEY (batch_id, document_id, line_item_index),
        FOREIGN KEY (batch_id, document_id)
            REFERENCES {TABLE_NAME} (batch_id, document_id) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_METADATA_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        variant TEXT,
        show_description BOOLEAN NOT NULL,
        total_placement TEXT,
        continued_across_pages BOOLEAN NOT NULL,
        PRIMARY KEY (batch_id, document_id),
        FOREIGN KEY (batch_id, document_id)
            REFERENCES {TABLE_NAME} (batch_id, document_id) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE_COLUMNS_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        column_index INTEGER NOT NULL,
        column_key TEXT,
        column_label TEXT,
        is_numeric BOOLEAN NOT NULL,
        PRIMARY KEY (batch_id, document_id, column_index),
        FOREIGN KEY (batch_id, document_id)
            REFERENCES {TABLE_NAME} (batch_id, document_id) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {PAGES_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        pdf_page_number INTEGER NOT NULL,
        document_page_number INTEGER,
        document_page_count INTEGER,
        sample_id TEXT,
        table_continues_from_previous_page BOOLEAN NOT NULL,
        table_continues_after_page BOOLEAN NOT NULL,
        renders_totals BOOLEAN NOT NULL,
        total_placement TEXT,
        notes_near_table_bounds BOOLEAN NOT NULL,
        PRIMARY KEY (batch_id, document_id, pdf_page_number),
        FOREIGN KEY (batch_id, document_id)
            REFERENCES {TABLE_NAME} (batch_id, document_id) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {PAGE_LINE_ITEMS_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        pdf_page_number INTEGER NOT NULL,
        line_item_index INTEGER NOT NULL,
        line_number INTEGER NOT NULL,
        PRIMARY KEY (batch_id, document_id, pdf_page_number, line_item_index),
        FOREIGN KEY (batch_id, document_id, pdf_page_number)
            REFERENCES {PAGES_TABLE_NAME} (batch_id, document_id, pdf_page_number) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {PAGE_COMPONENTS_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        pdf_page_number INTEGER NOT NULL,
        component_index INTEGER NOT NULL,
        component_kind TEXT NOT NULL,
        bbox_x_mm NUMERIC(10,2),
        bbox_y_mm NUMERIC(10,2),
        bbox_width_mm NUMERIC(10,2),
        bbox_height_mm NUMERIC(10,2),
        PRIMARY KEY (batch_id, document_id, pdf_page_number, component_index),
        FOREIGN KEY (batch_id, document_id, pdf_page_number)
            REFERENCES {PAGES_TABLE_NAME} (batch_id, document_id, pdf_page_number) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {SAMPLE_IDS_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        sample_index INTEGER NOT NULL,
        sample_id TEXT NOT NULL,
        PRIMARY KEY (batch_id, document_id, sample_index),
        FOREIGN KEY (batch_id, document_id)
            REFERENCES {TABLE_NAME} (batch_id, document_id) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS {CHALLENGE_TAGS_TABLE_NAME} (
        batch_id TEXT NOT NULL,
        document_id TEXT NOT NULL,
        challenge_tag TEXT NOT NULL,
        PRIMARY KEY (batch_id, document_id, challenge_tag),
        FOREIGN KEY (batch_id, document_id)
            REFERENCES {TABLE_NAME} (batch_id, document_id) ON DELETE CASCADE
    )
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_batch_invoice_number
    ON {TABLE_NAME} (batch_id, invoice_number)
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_{TABLE_NAME}_batch_vendor_invoice_number
    ON {TABLE_NAME} (batch_id, vendor_name, invoice_number)
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_{LINE_ITEMS_TABLE_NAME}_batch_document
    ON {LINE_ITEMS_TABLE_NAME} (batch_id, document_id)
    """,
    f"""
    CREATE INDEX IF NOT EXISTS idx_{PAGES_TABLE_NAME}_batch_document
    ON {PAGES_TABLE_NAME} (batch_id, document_id)
    """,
    f"""
    CREATE OR REPLACE VIEW {SUMMARY_VIEW_NAME} AS
    SELECT
        truth.id,
        truth.batch_id,
        truth.pdf_filename,
        truth.pdf_page_count,
        truth.manifest_filename,
        truth.document_id,
        truth.suite,
        truth.fixture_slug,
        truth.template_slug,
        truth.paper_slug,
        truth.invoice_number,
        truth.vendor_name,
        truth.customer_name,
        truth.issue_date,
        truth.due_date,
        truth.currency,
        truth.total,
        truth.balance_due,
        COALESCE(line_counts.line_item_count, 0) AS line_item_count,
        truth.created_at
    FROM {TABLE_NAME} truth
    LEFT JOIN (
        SELECT batch_id, document_id, COUNT(*) AS line_item_count
        FROM {LINE_ITEMS_TABLE_NAME}
        GROUP BY batch_id, document_id
    ) line_counts
        ON line_counts.batch_id = truth.batch_id
       AND line_counts.document_id = truth.document_id
    """,
)

INSERT_COLUMNS = (
    "batch_id",
    "pdf_filename",
    "pdf_page_count",
    "manifest_filename",
    "manifest_schema_version",
    "document_id",
    "suite",
    "fixture_slug",
    "template_slug",
    "paper_slug",
    "invoice_number",
    "invoice_number_style",
    "vendor_name",
    "vendor_line1",
    "vendor_city",
    "vendor_email",
    "vendor_tax_id",
    "customer_name",
    "customer_line1",
    "customer_city",
    "issue_date",
    "issue_date_display",
    "due_date",
    "due_date_display",
    "purchase_order",
    "terms",
    "status",
    "currency",
    "subtotal",
    "discount",
    "tax",
    "shipping",
    "paid",
    "total",
    "balance_due",
    "labels",
    "line_items",
    "raw_document",
    "generation_params",
)
INSERT_PLACEHOLDERS = ", ".join(
    "%s::jsonb" if column in JSON_COLUMNS else "%s"
    for column in INSERT_COLUMNS
)
INSERT_SQL = f"""
INSERT INTO {TABLE_NAME} ({", ".join(INSERT_COLUMNS)})
VALUES ({INSERT_PLACEHOLDERS})
"""

LABEL_INSERT_SQL = f"""
INSERT INTO {LABELS_TABLE_NAME} (
    batch_id, document_id, label_key, label_text
) VALUES (%s, %s, %s, %s)
"""

AMOUNT_INSERT_SQL = f"""
INSERT INTO {AMOUNTS_TABLE_NAME} (
    batch_id, document_id, amount_key, amount_value, visible_value, display_value
) VALUES (%s, %s, %s, %s, %s, %s)
"""

LINE_ITEM_INSERT_SQL = f"""
INSERT INTO {LINE_ITEMS_TABLE_NAME} (
    batch_id,
    document_id,
    line_item_index,
    line_number,
    sku,
    hsn,
    item_name,
    description,
    quantity,
    quantity_display,
    unit_price_value,
    unit_price_visible_value,
    unit_price_display,
    amount_value,
    amount_visible_value,
    amount_display,
    service_date,
    service_date_display
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

TABLE_METADATA_INSERT_SQL = f"""
INSERT INTO {TABLE_METADATA_TABLE_NAME} (
    batch_id,
    document_id,
    variant,
    show_description,
    total_placement,
    continued_across_pages
) VALUES (%s, %s, %s, %s, %s, %s)
"""

TABLE_COLUMN_INSERT_SQL = f"""
INSERT INTO {TABLE_COLUMNS_TABLE_NAME} (
    batch_id,
    document_id,
    column_index,
    column_key,
    column_label,
    is_numeric
) VALUES (%s, %s, %s, %s, %s, %s)
"""

PAGE_INSERT_SQL = f"""
INSERT INTO {PAGES_TABLE_NAME} (
    batch_id,
    document_id,
    pdf_page_number,
    document_page_number,
    document_page_count,
    sample_id,
    table_continues_from_previous_page,
    table_continues_after_page,
    renders_totals,
    total_placement,
    notes_near_table_bounds
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

PAGE_LINE_ITEM_INSERT_SQL = f"""
INSERT INTO {PAGE_LINE_ITEMS_TABLE_NAME} (
    batch_id, document_id, pdf_page_number, line_item_index, line_number
) VALUES (%s, %s, %s, %s, %s)
"""

PAGE_COMPONENT_INSERT_SQL = f"""
INSERT INTO {PAGE_COMPONENTS_TABLE_NAME} (
    batch_id,
    document_id,
    pdf_page_number,
    component_index,
    component_kind,
    bbox_x_mm,
    bbox_y_mm,
    bbox_width_mm,
    bbox_height_mm
) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
"""

SAMPLE_ID_INSERT_SQL = f"""
INSERT INTO {SAMPLE_IDS_TABLE_NAME} (
    batch_id, document_id, sample_index, sample_id
) VALUES (%s, %s, %s, %s)
"""

CHALLENGE_TAG_INSERT_SQL = f"""
INSERT INTO {CHALLENGE_TAGS_TABLE_NAME} (
    batch_id, document_id, challenge_tag
) VALUES (%s, %s, %s)
"""


def build_invoice_truth_rows(
    *,
    batch_id: str,
    pdf_count: int = DEFAULT_PDF_COUNT,
    seed: int = DEFAULT_SEED,
    today: date | None = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for entry in iter_invoice_corpus(
        pdf_count=pdf_count,
        seed=seed,
        today=today,
    ):
        rows.extend(_rows_for_corpus_entry(batch_id=batch_id, entry=entry))
    _validate_unique_rows(rows)
    return rows


def build_invoice_truth_rows_from_manifest_dir(
    *,
    batch_id: str,
    manifest_dir: Path = DEFAULT_MANIFEST_DIR,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for manifest_path in _manifest_paths(manifest_dir):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        rows.extend(
            _rows_for_manifest(
                batch_id=batch_id,
                manifest=manifest,
                manifest_path=manifest_path,
            )
        )
    _validate_unique_rows(rows)
    return rows


def initialize_invoice_truth_schema(conn: Any) -> None:
    for statement in SCHEMA_SQL:
        conn.execute(statement)


def replace_invoice_truth_batch(
    conn: Any,
    *,
    batch_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    with conn.transaction():
        initialize_invoice_truth_schema(conn)
        conn.execute(f"DELETE FROM {TABLE_NAME} WHERE batch_id = %s", (batch_id,))
        for row in rows:
            conn.execute(INSERT_SQL, _insert_params(row))
            _insert_normalized_child_rows(conn, row)
    return len(rows)


def seed_invoice_truth_database(
    *,
    database_url: str,
    batch_id: str,
    rows: Sequence[Mapping[str, Any]],
) -> int:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError(
            "Install psycopg before seeding invoice truth data: pip install 'psycopg[binary]'"
        ) from exc

    with psycopg.connect(database_url) as conn:
        return replace_invoice_truth_batch(conn, batch_id=batch_id, rows=rows)


def resolve_database_url(
    explicit_database_url: str | None,
    *,
    env_path: Path = ROOT / ".env",
) -> str | None:
    return (
        explicit_database_url
        or os.environ.get("DATABASE_URL")
        or _load_env_file(env_path).get("DATABASE_URL")
    )


def _rows_for_corpus_entry(
    *,
    batch_id: str,
    entry: InvoiceCorpusEntry,
) -> list[dict[str, Any]]:
    manifest = build_invoice_manifest(
        entry.samples,
        pdf_filename=entry.pdf_filename,
        suite=entry.suite,
        fixture_slug=entry.fixture_slug,
    )
    samples_by_id = {str(sample["id"]): sample for sample in entry.samples}
    rows = []
    for document in manifest["documents"]:
        sample = _first_sample_for_document(document, samples_by_id)
        generation_params = {
            **entry.generation_params,
            "source": "generator",
            "pdf_filename": entry.pdf_filename,
            "document_id": document["document_id"],
            "sample_ids": list(document.get("sample_ids", [])),
        }
        rows.append(
            _row_from_manifest_document(
                batch_id=batch_id,
                pdf_filename=entry.pdf_filename,
                pdf_page_count=manifest.get("pdf", {}).get("page_count"),
                manifest_filename=None,
                manifest_schema_version=manifest.get("schema_version"),
                suite=entry.suite,
                fixture_slug=entry.fixture_slug,
                document=document,
                template_slug=str(sample["template"]["slug"]),
                paper_slug=str(sample["paper"]["slug"]),
                invoice_number_style=str(sample["data"]["invoice_number_style"]),
                generation_params=generation_params,
            )
        )
    return rows


def _rows_for_manifest(
    *,
    batch_id: str,
    manifest: Mapping[str, Any],
    manifest_path: Path | None = None,
) -> list[dict[str, Any]]:
    pdf = manifest.get("pdf") if isinstance(manifest.get("pdf"), dict) else {}
    pdf_filename = str(pdf.get("filename") or _pdf_filename_from_manifest_path(manifest_path))
    suite = str(manifest.get("suite") or "unknown")
    fixture_slug = manifest.get("fixture_slug")
    if fixture_slug is not None:
        fixture_slug = str(fixture_slug)
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise ValueError(f"Manifest {manifest_path or pdf_filename} does not contain a documents list.")

    rows = []
    for document in documents:
        if not isinstance(document, dict):
            raise ValueError(f"Manifest {manifest_path or pdf_filename} contains a non-object document.")
        inferred = _infer_manifest_document_metadata(document)
        generation_params = {
            "source": "manifest",
            "manifest_filename": manifest_path.name if manifest_path else None,
            "manifest_path": str(manifest_path) if manifest_path else None,
            "manifest_schema_version": manifest.get("schema_version"),
            "pdf_filename": pdf_filename,
            "pdf_page_count": pdf.get("page_count"),
            "suite": suite,
            "fixture_slug": fixture_slug,
            "document_id": document.get("document_id"),
            "sample_ids": list(document.get("sample_ids", [])),
            **inferred,
        }
        rows.append(
            _row_from_manifest_document(
                batch_id=batch_id,
                pdf_filename=pdf_filename,
                pdf_page_count=pdf.get("page_count"),
                manifest_filename=manifest_path.name if manifest_path else None,
                manifest_schema_version=manifest.get("schema_version"),
                suite=suite,
                fixture_slug=fixture_slug,
                document=document,
                template_slug=str(inferred.get("template_slug") or "unknown"),
                paper_slug=str(inferred.get("paper_slug") or "unknown"),
                invoice_number_style=str(inferred.get("invoice_number_style") or "unknown"),
                generation_params=generation_params,
            )
        )
    return rows


def _row_from_manifest_document(
    *,
    batch_id: str,
    pdf_filename: str,
    pdf_page_count: Any,
    manifest_filename: str | None,
    manifest_schema_version: Any,
    suite: str,
    fixture_slug: str | None,
    document: Mapping[str, Any],
    template_slug: str,
    paper_slug: str,
    invoice_number_style: str,
    generation_params: Mapping[str, Any],
) -> dict[str, Any]:
    seller = _entity(document, "seller", {})
    buyer = _entity(document, "buyer", {})
    amounts = document["amounts"]
    return {
        "batch_id": batch_id,
        "pdf_filename": pdf_filename,
        "pdf_page_count": _int_or_none(pdf_page_count),
        "manifest_filename": manifest_filename,
        "manifest_schema_version": _int_or_none(manifest_schema_version),
        "document_id": str(document["document_id"]),
        "suite": suite,
        "fixture_slug": fixture_slug,
        "template_slug": template_slug,
        "paper_slug": paper_slug,
        "invoice_number": str(document["invoice_number"]),
        "invoice_number_style": invoice_number_style,
        "vendor_name": str(seller.get("name") or ""),
        "vendor_line1": seller.get("line1"),
        "vendor_city": seller.get("city"),
        "vendor_email": seller.get("email"),
        "vendor_tax_id": seller.get("tax_id"),
        "customer_name": str(buyer.get("name") or ""),
        "customer_line1": buyer.get("line1"),
        "customer_city": buyer.get("city"),
        "issue_date": _date_value(document["issue_date"]["value"]),
        "issue_date_display": _wrapped_display(document["issue_date"]),
        "due_date": _date_value(document["due_date"]["value"]),
        "due_date_display": _wrapped_display(document["due_date"]),
        "purchase_order": document.get("purchase_order"),
        "terms": document.get("terms"),
        "status": document.get("status"),
        "currency": str(document["currency"]),
        "subtotal": _amount_value(amounts, "subtotal"),
        "discount": _amount_value(amounts, "discount"),
        "tax": _amount_value(amounts, "tax"),
        "shipping": _amount_value(amounts, "shipping"),
        "paid": _amount_value(amounts, "paid"),
        "total": _amount_value(amounts, "total"),
        "balance_due": _amount_value(amounts, "balance_due"),
        "labels": dict(document.get("labels", {})),
        "line_items": list(document.get("line_items", [])),
        "raw_document": dict(document),
        "generation_params": dict(generation_params),
    }


def _first_sample_for_document(
    document: Mapping[str, Any],
    samples_by_id: Mapping[str, dict[str, Any]],
) -> dict[str, Any]:
    for sample_id in document.get("sample_ids", []):
        sample = samples_by_id.get(str(sample_id))
        if sample is not None:
            return sample
    raise ValueError(f"Could not find source sample for document {document.get('document_id')}")


def _entity(
    document: Mapping[str, Any],
    key: str,
    fallback: Any,
) -> dict[str, Any]:
    entity = document.get(key)
    if isinstance(entity, dict):
        return entity
    if isinstance(fallback, dict):
        return fallback
    return {}


def _amount_value(amounts: Mapping[str, Any], key: str) -> Decimal:
    amount = amounts[key]
    if isinstance(amount, dict):
        amount = amount["value"]
    return _money_decimal(amount)


def _money_decimal(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _decimal_or_none(value: Any, quant: Decimal | None = None) -> Decimal | None:
    if value is None or value == "":
        return None
    decimal_value = Decimal(str(value))
    if quant is not None:
        return decimal_value.quantize(quant, rounding=ROUND_HALF_UP)
    return decimal_value


def _date_value(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _optional_date_value(value: Any) -> date | None:
    if value is None or value == "":
        return None
    return _date_value(value)


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _wrapped_display(value: Any) -> str | None:
    if isinstance(value, Mapping):
        display = value.get("display")
        if display is not None:
            return str(display)
    return None


def _wrapped_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("value")
    return value


def _money_parts(value: Any) -> tuple[Decimal | None, str | None, str | None]:
    if isinstance(value, Mapping):
        return (
            _decimal_or_none(value.get("value"), MONEY_QUANT),
            _text_or_none(value.get("visible_value")),
            _text_or_none(value.get("display")),
        )
    return (_decimal_or_none(value, MONEY_QUANT), None, None)


def _text_or_none(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _manifest_paths(manifest_dir: Path) -> list[Path]:
    if not manifest_dir.exists():
        raise ValueError(f"Manifest directory does not exist: {manifest_dir}")
    paths = sorted(
        path
        for path in manifest_dir.iterdir()
        if path.is_file() and path.name.endswith(".manifest.json")
    )
    if not paths:
        raise ValueError(f"No .manifest.json files found in {manifest_dir}")
    return paths


def _pdf_filename_from_manifest_path(manifest_path: Path | None) -> str:
    if manifest_path is None:
        return "unknown.pdf"
    suffix = ".manifest.json"
    if manifest_path.name.endswith(suffix):
        return manifest_path.name[: -len(suffix)] + ".pdf"
    return manifest_path.with_suffix(".pdf").name


def _infer_manifest_document_metadata(document: Mapping[str, Any]) -> dict[str, Any]:
    source_ids = [
        str(document.get("document_id") or ""),
        *[str(sample_id) for sample_id in document.get("sample_ids", [])],
    ]
    for source_id in source_ids:
        metadata = _metadata_from_sample_id(source_id)
        if metadata:
            return metadata
    return {
        "template_slug": "unknown",
        "paper_slug": "unknown",
        "sample_seed": None,
        "variation_index": None,
        "invoice_number_style": "unknown",
    }


def _metadata_from_sample_id(source_id: str) -> dict[str, Any] | None:
    for template_index, template in sorted(
        enumerate(BASE_TEMPLATES),
        key=lambda item: len(item[1].slug),
        reverse=True,
    ):
        for paper in sorted(PAPER_FORMATS, key=lambda item: len(item.slug), reverse=True):
            prefix = f"{template.slug}-{paper.slug}-"
            if not source_id.startswith(prefix):
                continue
            parts = source_id[len(prefix) :].split("-")
            if len(parts) < 2:
                continue
            try:
                sample_seed = int(parts[0])
                variation_index = int(parts[1])
            except ValueError:
                continue
            profile = CAPTURE_PROFILES[
                (template_index + variation_index) % len(CAPTURE_PROFILES)
            ]
            return {
                "template_slug": template.slug,
                "paper_slug": paper.slug,
                "sample_seed": sample_seed,
                "variation_index": variation_index,
                "invoice_number_style": str(profile["invoice_number_style"]),
            }
    return None


def _insert_params(row: Mapping[str, Any]) -> tuple[Any, ...]:
    params: list[Any] = []
    for column in INSERT_COLUMNS:
        value = row[column]
        if column in JSON_COLUMNS:
            value = json.dumps(value, sort_keys=True, separators=(",", ":"))
        params.append(value)
    return tuple(params)


def _insert_normalized_child_rows(conn: Any, row: Mapping[str, Any]) -> None:
    for params in _label_insert_params(row):
        conn.execute(LABEL_INSERT_SQL, params)
    for params in _amount_insert_params(row):
        conn.execute(AMOUNT_INSERT_SQL, params)
    for params in _line_item_insert_params(row):
        conn.execute(LINE_ITEM_INSERT_SQL, params)
    table_params = _table_metadata_insert_params(row)
    if table_params is not None:
        conn.execute(TABLE_METADATA_INSERT_SQL, table_params)
    for params in _table_column_insert_params(row):
        conn.execute(TABLE_COLUMN_INSERT_SQL, params)
    for params in _sample_id_insert_params(row):
        conn.execute(SAMPLE_ID_INSERT_SQL, params)
    for params in _challenge_tag_insert_params(row):
        conn.execute(CHALLENGE_TAG_INSERT_SQL, params)
    for page_params, line_number_params, component_params in _page_insert_params(row):
        conn.execute(PAGE_INSERT_SQL, page_params)
        for params in line_number_params:
            conn.execute(PAGE_LINE_ITEM_INSERT_SQL, params)
        for params in component_params:
            conn.execute(PAGE_COMPONENT_INSERT_SQL, params)


def _label_insert_params(row: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    labels = row.get("labels")
    if not isinstance(labels, Mapping):
        return []
    return [
        (row["batch_id"], row["document_id"], str(key), str(value))
        for key, value in sorted(labels.items())
        if value is not None
    ]


def _amount_insert_params(row: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    document = row["raw_document"]
    amounts = document.get("amounts") if isinstance(document, Mapping) else None
    if not isinstance(amounts, Mapping):
        return []
    params = []
    for key in MONEY_KEYS:
        if key not in amounts:
            continue
        value, visible_value, display_value = _money_parts(amounts[key])
        if value is None:
            continue
        params.append((row["batch_id"], row["document_id"], key, value, visible_value, display_value))
    return params


def _line_item_insert_params(row: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    line_items = row.get("line_items")
    if not isinstance(line_items, list):
        return []
    params = []
    for line_item_index, item in enumerate(line_items, start=1):
        if not isinstance(item, Mapping):
            continue
        unit_price_value, unit_price_visible, unit_price_display = _money_parts(item.get("unit_price"))
        amount_value, amount_visible, amount_display = _money_parts(item.get("amount"))
        service_date = item.get("service_date")
        service_date_value = _wrapped_value(service_date)
        params.append(
            (
                row["batch_id"],
                row["document_id"],
                line_item_index,
                _int_or_none(item.get("line")),
                _text_or_none(item.get("sku")),
                _text_or_none(item.get("hsn")),
                _text_or_none(item.get("name")),
                _text_or_none(item.get("description")),
                _decimal_or_none(item.get("quantity")),
                _text_or_none(item.get("quantity_display")),
                unit_price_value,
                unit_price_visible,
                unit_price_display,
                amount_value,
                amount_visible,
                amount_display,
                _optional_date_value(service_date_value),
                _wrapped_display(service_date),
            )
        )
    return params


def _table_metadata_insert_params(row: Mapping[str, Any]) -> tuple[Any, ...] | None:
    table = _document_table(row)
    if table is None:
        return None
    return (
        row["batch_id"],
        row["document_id"],
        _text_or_none(table.get("variant")),
        bool(table.get("show_description")),
        _text_or_none(table.get("total_placement")),
        bool(table.get("continued_across_pages")),
    )


def _table_column_insert_params(row: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    table = _document_table(row)
    columns = table.get("columns") if table is not None else None
    if not isinstance(columns, list):
        return []
    params = []
    for column_index, column in enumerate(columns, start=1):
        if not isinstance(column, Mapping):
            continue
        params.append(
            (
                row["batch_id"],
                row["document_id"],
                column_index,
                _text_or_none(column.get("key")),
                _text_or_none(column.get("label")),
                bool(column.get("numeric")),
            )
        )
    return params


def _sample_id_insert_params(row: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    document = row["raw_document"]
    sample_ids = document.get("sample_ids") if isinstance(document, Mapping) else None
    if not isinstance(sample_ids, list):
        return []
    return [
        (row["batch_id"], row["document_id"], index, str(sample_id))
        for index, sample_id in enumerate(sample_ids, start=1)
    ]


def _challenge_tag_insert_params(row: Mapping[str, Any]) -> list[tuple[Any, ...]]:
    document = row["raw_document"]
    challenge_tags = document.get("challenge_tags") if isinstance(document, Mapping) else None
    if not isinstance(challenge_tags, list):
        return []
    return [
        (row["batch_id"], row["document_id"], str(tag))
        for tag in sorted({str(tag) for tag in challenge_tags})
    ]


def _page_insert_params(
    row: Mapping[str, Any],
) -> list[tuple[tuple[Any, ...], list[tuple[Any, ...]], list[tuple[Any, ...]]]]:
    document = row["raw_document"]
    pages = document.get("pages") if isinstance(document, Mapping) else None
    if not isinstance(pages, list):
        return []
    page_params = []
    for page_index, page in enumerate(pages, start=1):
        if not isinstance(page, Mapping):
            continue
        pdf_page_number = _int_or_none(page.get("pdf_page_number")) or page_index
        line_number_params = _page_line_number_insert_params(row, page, pdf_page_number)
        component_params = _component_insert_params(row, page, pdf_page_number)
        page_params.append(
            (
                (
                    row["batch_id"],
                    row["document_id"],
                    pdf_page_number,
                    _int_or_none(page.get("document_page_number")),
                    _int_or_none(page.get("document_page_count")),
                    _text_or_none(page.get("sample_id")),
                    bool(page.get("table_continues_from_previous_page")),
                    bool(page.get("table_continues_after_page")),
                    bool(page.get("renders_totals")),
                    _text_or_none(page.get("total_placement")),
                    bool(page.get("notes_near_table_bounds")),
                ),
                line_number_params,
                component_params,
            )
        )
    return page_params


def _page_line_number_insert_params(
    row: Mapping[str, Any],
    page: Mapping[str, Any],
    pdf_page_number: int,
) -> list[tuple[Any, ...]]:
    line_numbers = page.get("line_item_numbers")
    if not isinstance(line_numbers, list):
        return []
    params = []
    for line_item_index, line_number in enumerate(line_numbers, start=1):
        parsed_line_number = _int_or_none(line_number)
        if parsed_line_number is None:
            continue
        params.append(
            (
                row["batch_id"],
                row["document_id"],
                pdf_page_number,
                line_item_index,
                parsed_line_number,
            )
        )
    return params


def _component_insert_params(
    row: Mapping[str, Any],
    page: Mapping[str, Any],
    pdf_page_number: int,
) -> list[tuple[Any, ...]]:
    components = page.get("components")
    if not isinstance(components, list):
        return []
    params = []
    for component_index, component in enumerate(components, start=1):
        if not isinstance(component, Mapping):
            continue
        bbox = component.get("bbox_mm")
        if not isinstance(bbox, list):
            bbox = []
        padded_bbox = [*bbox[:4], None, None, None, None][:4]
        params.append(
            (
                row["batch_id"],
                row["document_id"],
                pdf_page_number,
                component_index,
                str(component.get("kind") or ""),
                _decimal_or_none(padded_bbox[0], Decimal("0.01")),
                _decimal_or_none(padded_bbox[1], Decimal("0.01")),
                _decimal_or_none(padded_bbox[2], Decimal("0.01")),
                _decimal_or_none(padded_bbox[3], Decimal("0.01")),
            )
        )
    return params


def _document_table(row: Mapping[str, Any]) -> Mapping[str, Any] | None:
    document = row["raw_document"]
    table = document.get("table") if isinstance(document, Mapping) else None
    if isinstance(table, Mapping):
        return table
    return None


def _validate_unique_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    document_keys = set()
    vendor_invoice_keys = set()
    for row in rows:
        document_key = (row["batch_id"], row["document_id"])
        vendor_invoice_key = (row["batch_id"], row["vendor_name"], row["invoice_number"])
        if document_key in document_keys:
            raise ValueError(f"Duplicate document truth key: {document_key!r}")
        if vendor_invoice_key in vendor_invoice_keys:
            raise ValueError(f"Duplicate vendor invoice truth key: {vendor_invoice_key!r}")
        document_keys.add(document_key)
        vendor_invoice_keys.add(vendor_invoice_key)


def _load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _print_dry_run(rows: Sequence[Mapping[str, Any]]) -> None:
    print(
        json.dumps(
            {
                "dry_run": True,
                "row_count": len(rows),
                "sample_rows": [_dry_run_row(row) for row in rows[:3]],
            },
            indent=2,
            sort_keys=True,
        )
    )


def _dry_run_row(row: Mapping[str, Any]) -> dict[str, Any]:
    generation_params = row["generation_params"]
    return _json_ready(
        {
            "batch_id": row["batch_id"],
            "pdf_filename": row["pdf_filename"],
            "document_id": row["document_id"],
            "suite": row["suite"],
            "fixture_slug": row["fixture_slug"],
            "vendor_name": row["vendor_name"],
            "invoice_number": row["invoice_number"],
            "issue_date": row["issue_date"],
            "due_date": row["due_date"],
            "currency": row["currency"],
            "total": row["total"],
            "balance_due": row["balance_due"],
            "line_item_count": len(row["line_items"]),
            "generation": {
                "source": generation_params.get("source"),
                "pdf_count": generation_params.get("pdf_count"),
                "seed": generation_params.get("seed"),
                "date": generation_params.get("date"),
                "manifest_filename": generation_params.get("manifest_filename"),
            },
        }
    )


def _json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed deterministic truth rows for the generated invoice PDF corpus."
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL database URL. Defaults to DATABASE_URL from the environment or .env.",
    )
    parser.add_argument(
        "--batch-id",
        default="default",
        help="Batch identifier used to replace and join seeded truth rows.",
    )
    parser.add_argument(
        "--manifest-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing .manifest.json files to seed directly. "
            "When set, --pdf-count, --seed, and --date are not used."
        ),
    )
    parser.add_argument(
        "--pdf-count",
        type=int,
        default=DEFAULT_PDF_COUNT,
        help="Generator mode only: total PDF corpus count to mirror. Defaults to app.generate_test_pdfs.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Generator mode only: base seed matching app.generate_test_pdfs.",
    )
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        default=None,
        help="Generator mode only: anchor date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print row count and sample rows without writing to the database.",
    )
    args = parser.parse_args()

    try:
        if args.manifest_dir is not None:
            rows = build_invoice_truth_rows_from_manifest_dir(
                batch_id=args.batch_id,
                manifest_dir=args.manifest_dir,
            )
        else:
            rows = build_invoice_truth_rows(
                batch_id=args.batch_id,
                pdf_count=args.pdf_count,
                seed=args.seed,
                today=args.date,
            )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.dry_run:
        _print_dry_run(rows)
        return

    database_url = resolve_database_url(args.database_url)
    if not database_url:
        raise SystemExit("DATABASE_URL is required unless --dry-run is used.")

    try:
        inserted = seed_invoice_truth_database(
            database_url=database_url,
            batch_id=args.batch_id,
            rows=rows,
        )
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc

    print(f"Seeded {inserted} rows into {TABLE_NAME} for batch {args.batch_id!r}.")


if __name__ == "__main__":
    main()
