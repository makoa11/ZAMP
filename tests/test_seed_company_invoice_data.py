from __future__ import annotations

import json
import os
import tempfile
import unittest
from collections import Counter
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app.generate_test_pdfs import iter_invoice_corpus
from app.invoice_fixtures import build_invoice_manifest
from scripts.seed_company_invoice_data import (
    AMOUNTS_TABLE_NAME,
    CHALLENGE_TAGS_TABLE_NAME,
    INSERT_COLUMNS,
    LABELS_TABLE_NAME,
    LINE_ITEMS_TABLE_NAME,
    PAGES_TABLE_NAME,
    PAGE_COMPONENTS_TABLE_NAME,
    PAGE_LINE_ITEMS_TABLE_NAME,
    SAMPLE_IDS_TABLE_NAME,
    SUMMARY_VIEW_NAME,
    TABLE_NAME,
    TABLE_COLUMNS_TABLE_NAME,
    TABLE_METADATA_TABLE_NAME,
    build_invoice_truth_rows,
    build_invoice_truth_rows_from_manifest_dir,
    replace_invoice_truth_batch,
    resolve_database_url,
)


MONEY_COLUMNS = ("subtotal", "discount", "tax", "shipping", "paid", "total", "balance_due")
PARTIAL_PO_TAGS = {"partial_po_consumption", "split_po_billing"}
COMPARABLE_COLUMNS = (
    "batch_id",
    "pdf_filename",
    "pdf_page_count",
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
    *MONEY_COLUMNS,
    "labels",
    "line_items",
    "raw_document",
)


class _FakeTransaction:
    def __init__(self, conn: "_FakeConnection") -> None:
        self.conn = conn

    def __enter__(self) -> None:
        self.conn.transaction_events.append("enter")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.conn.transaction_events.append("exit")


class _FakeConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...] | None]] = []
        self.transaction_events: list[str] = []

    def transaction(self) -> _FakeTransaction:
        return _FakeTransaction(self)

    def execute(self, sql: str, params: tuple[object, ...] | None = None) -> None:
        self.executed.append((sql, params))


def _insert_params_for(conn: _FakeConnection, table_name: str) -> list[tuple[object, ...]]:
    return [
        params
        for sql, params in conn.executed
        if params is not None and sql.lstrip().startswith(f"INSERT INTO {table_name} (")
    ]


class SeedCompanyInvoiceDataTests(unittest.TestCase):
    def _write_manifest_corpus(
        self,
        root: Path,
        *,
        pdf_count: int,
        seed: int = 500,
        today: date = date(2026, 7, 7),
    ) -> None:
        for entry in iter_invoice_corpus(
            pdf_count=pdf_count,
            seed=seed,
            today=today,
        ):
            manifest = build_invoice_manifest(
                entry.samples,
                pdf_filename=entry.pdf_filename,
                suite=entry.suite,
                fixture_slug=entry.fixture_slug,
            )
            manifest_path = root / entry.pdf_filename.replace(".pdf", ".manifest.json")
            manifest_path.write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

    def test_build_invoice_truth_rows_maps_standard_invoice_fields(self) -> None:
        rows = build_invoice_truth_rows(
            batch_id="parser-test",
            pdf_count=2,
            seed=500,
            today=date(2026, 7, 7),
        )
        standard = next(row for row in rows if row["suite"] == "standard")

        self.assertEqual(standard["batch_id"], "parser-test")
        self.assertEqual(standard["pdf_filename"], "invoice-sample-0001-a4-ledger-clean.pdf")
        self.assertEqual(standard["template_slug"], "ledger-clean")
        self.assertEqual(standard["paper_slug"], "a4")
        self.assertEqual(standard["document_id"], standard["raw_document"]["document_id"])
        self.assertEqual(standard["invoice_number"], standard["raw_document"]["invoice_number"])
        self.assertEqual(standard["vendor_name"], standard["raw_document"]["seller"]["name"])
        self.assertEqual(standard["vendor_line1"], standard["raw_document"]["seller"]["line1"])
        self.assertEqual(standard["vendor_city"], standard["raw_document"]["seller"]["city"])
        self.assertEqual(standard["customer_name"], standard["raw_document"]["buyer"]["name"])
        self.assertEqual(standard["customer_line1"], standard["raw_document"]["buyer"]["line1"])
        self.assertEqual(standard["customer_city"], standard["raw_document"]["buyer"]["city"])
        self.assertIsInstance(standard["issue_date"], date)
        self.assertIsInstance(standard["due_date"], date)
        self.assertEqual(standard["issue_date_display"], standard["raw_document"]["issue_date"]["display"])
        self.assertEqual(standard["due_date_display"], standard["raw_document"]["due_date"]["display"])
        self.assertLessEqual(standard["issue_date"], standard["due_date"])
        self.assertIn("invoice_number", standard["labels"])
        self.assertGreater(len(standard["line_items"]), 0)
        self.assertEqual(standard["generation_params"]["seed"], 500)
        self.assertEqual(standard["generation_params"]["pdf_count"], 2)
        self.assertEqual(standard["generation_params"]["date"], "2026-07-07")

    def test_build_invoice_truth_rows_from_manifest_dir_maps_existing_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest_corpus(root, pdf_count=5)

            rows = build_invoice_truth_rows_from_manifest_dir(
                batch_id="parser-test",
                manifest_dir=root,
            )

        self.assertEqual(len(rows), 8)
        standard = rows[0]
        self.assertEqual(standard["pdf_filename"], "invoice-sample-0001-a4-ledger-clean.pdf")
        self.assertEqual(standard["pdf_page_count"], 1)
        self.assertEqual(standard["manifest_filename"], "invoice-sample-0001-a4-ledger-clean.manifest.json")
        self.assertEqual(standard["manifest_schema_version"], 1)
        self.assertEqual(standard["template_slug"], "ledger-clean")
        self.assertEqual(standard["paper_slug"], "a4")
        self.assertEqual(standard["invoice_number_style"], "prefix-year-month")
        self.assertEqual(standard["generation_params"]["source"], "manifest")
        self.assertEqual(
            standard["generation_params"]["manifest_filename"],
            "invoice-sample-0001-a4-ledger-clean.manifest.json",
        )

    def test_manifest_partial_po_ap_tags_are_seeded_to_raw_document_and_child_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest_corpus(root, pdf_count=5)

            rows = build_invoice_truth_rows_from_manifest_dir(
                batch_id="parser-test",
                manifest_dir=root,
            )

        partial = next(row for row in rows if row["purchase_order"] == "PO-10000-PART")
        raw_document = partial["raw_document"]
        conn = _FakeConnection()

        replace_invoice_truth_batch(conn, batch_id="parser-test", rows=[partial])

        inserted_tags = {
            params[2]
            for params in _insert_params_for(conn, CHALLENGE_TAGS_TABLE_NAME)
        }
        self.assertTrue(PARTIAL_PO_TAGS.issubset(set(raw_document["challenge_tags"])))
        self.assertEqual(raw_document["ap_context"]["scenario"], "split_po_partial_billing")
        self.assertEqual(raw_document["ap_context"]["expected"]["decision"], "approve_partial_consumption")
        self.assertEqual(raw_document["ap_context"]["expected"]["remaining_after_invoice"], "3000.00")
        self.assertTrue(raw_document["ap_context"]["expected"]["requires_client_vendor_match"])
        self.assertEqual(
            raw_document["ap_context"]["context"]["previous_related_document"]["invoice_number"],
            "INV-PO10000-0001",
        )
        self.assertEqual(
            raw_document["ap_context"]["context"]["client_database"]["previous_related_documents"][0]["applied_to_po"],
            "3000.00",
        )
        self.assertEqual(
            raw_document["ap_context"]["context"]["vendor_database"]["previous_related_documents"][0]["applied_to_po"],
            "3000.00",
        )
        self.assertEqual(raw_document["edge_cases"][0], raw_document["ap_context"])
        self.assertTrue(PARTIAL_PO_TAGS.issubset(inserted_tags))

    def test_manifest_dir_rows_match_generated_truth_for_comparable_fields(self) -> None:
        expected = build_invoice_truth_rows(
            batch_id="parser-test",
            pdf_count=8,
            seed=500,
            today=date(2026, 7, 7),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            self._write_manifest_corpus(root, pdf_count=8)

            actual = build_invoice_truth_rows_from_manifest_dir(
                batch_id="parser-test",
                manifest_dir=root,
            )

        self.assertEqual(len(actual), len(expected))
        for actual_row, expected_row in zip(actual, expected):
            self.assertEqual(
                {column: actual_row[column] for column in COMPARABLE_COLUMNS},
                {column: expected_row[column] for column in COMPARABLE_COLUMNS},
            )

    def test_money_values_are_decimal_normalized_from_manifest_amounts(self) -> None:
        row = build_invoice_truth_rows(
            batch_id="parser-test",
            pdf_count=2,
            seed=500,
            today=date(2026, 7, 7),
        )[0]

        for column in MONEY_COLUMNS:
            self.assertIsInstance(row[column], Decimal)
            self.assertEqual(row[column].as_tuple().exponent, -2)
            self.assertEqual(row[column], Decimal(row["raw_document"]["amounts"][column]["value"]))

    def test_stress_fixture_document_counts_are_seeded_per_document(self) -> None:
        rows = build_invoice_truth_rows(
            batch_id="parser-test",
            pdf_count=8,
            seed=500,
            today=date(2026, 7, 7),
        )
        stress_counts = Counter(row["pdf_filename"] for row in rows if row["suite"] == "stress")

        self.assertEqual(len(rows), 15)
        self.assertEqual(
            stress_counts,
            Counter(
                {
                    "invoice-stress-0001-multipage-continuation.pdf": 1,
                    "invoice-stress-0002-ambiguous-labels-mixed-totals.pdf": 4,
                    "invoice-stress-0003-currency-glyphs.pdf": 5,
                }
            ),
        )
        multipage = next(
            row
            for row in rows
            if row["pdf_filename"] == "invoice-stress-0001-multipage-continuation.pdf"
        )
        self.assertEqual(len(multipage["line_items"]), 24)
        self.assertEqual(len(multipage["raw_document"]["pages"]), 3)
        self.assertTrue(multipage["raw_document"]["table"]["continued_across_pages"])

    def test_replace_batch_creates_schema_deletes_batch_and_inserts_rows_in_transaction(self) -> None:
        rows = build_invoice_truth_rows(
            batch_id="parser-test",
            pdf_count=2,
            seed=500,
            today=date(2026, 7, 7),
        )
        conn = _FakeConnection()

        inserted = replace_invoice_truth_batch(conn, batch_id="parser-test", rows=rows)

        self.assertEqual(inserted, len(rows))
        self.assertEqual(conn.transaction_events, ["enter", "exit"])
        self.assertIn(f"CREATE TABLE IF NOT EXISTS {TABLE_NAME}", conn.executed[0][0])
        self.assertTrue(
            any(f"CREATE TABLE IF NOT EXISTS {LINE_ITEMS_TABLE_NAME}" in sql for sql, _ in conn.executed)
        )
        self.assertTrue(any(f"CREATE OR REPLACE VIEW {SUMMARY_VIEW_NAME}" in sql for sql, _ in conn.executed))
        deletes = [item for item in conn.executed if f"DELETE FROM {TABLE_NAME}" in item[0]]
        inserts = _insert_params_for(conn, TABLE_NAME)
        self.assertEqual(len(deletes), 1)
        self.assertEqual(deletes[0][1], ("parser-test",))
        self.assertEqual(len(inserts), len(rows))

        labels_index = INSERT_COLUMNS.index("labels")
        raw_document_index = INSERT_COLUMNS.index("raw_document")
        first_params = inserts[0]
        self.assertIsInstance(first_params[labels_index], str)
        self.assertIsInstance(first_params[raw_document_index], str)
        self.assertEqual(json.loads(first_params[labels_index]), rows[0]["labels"])
        self.assertEqual(json.loads(first_params[raw_document_index]), rows[0]["raw_document"])

    def test_replace_batch_inserts_normalized_manifest_child_rows(self) -> None:
        row = build_invoice_truth_rows(
            batch_id="parser-test",
            pdf_count=2,
            seed=500,
            today=date(2026, 7, 7),
        )[0]
        conn = _FakeConnection()

        replace_invoice_truth_batch(conn, batch_id="parser-test", rows=[row])

        self.assertEqual(len(_insert_params_for(conn, LABELS_TABLE_NAME)), len(row["labels"]))
        self.assertEqual(len(_insert_params_for(conn, AMOUNTS_TABLE_NAME)), len(MONEY_COLUMNS))
        self.assertEqual(len(_insert_params_for(conn, LINE_ITEMS_TABLE_NAME)), len(row["line_items"]))
        self.assertEqual(len(_insert_params_for(conn, TABLE_METADATA_TABLE_NAME)), 1)
        self.assertEqual(
            len(_insert_params_for(conn, TABLE_COLUMNS_TABLE_NAME)),
            len(row["raw_document"]["table"]["columns"]),
        )
        self.assertEqual(
            len(_insert_params_for(conn, SAMPLE_IDS_TABLE_NAME)),
            len(row["raw_document"]["sample_ids"]),
        )
        self.assertEqual(
            len(_insert_params_for(conn, CHALLENGE_TAGS_TABLE_NAME)),
            len(set(row["raw_document"]["challenge_tags"])),
        )
        self.assertEqual(
            len(_insert_params_for(conn, PAGES_TABLE_NAME)),
            len(row["raw_document"]["pages"]),
        )
        self.assertEqual(
            len(_insert_params_for(conn, PAGE_LINE_ITEMS_TABLE_NAME)),
            sum(len(page["line_item_numbers"]) for page in row["raw_document"]["pages"]),
        )
        self.assertEqual(
            len(_insert_params_for(conn, PAGE_COMPONENTS_TABLE_NAME)),
            sum(len(page["components"]) for page in row["raw_document"]["pages"]),
        )

        first_amount = _insert_params_for(conn, AMOUNTS_TABLE_NAME)[0]
        self.assertEqual(first_amount[:3], ("parser-test", row["document_id"], "subtotal"))
        self.assertEqual(first_amount[3], row["subtotal"])

        first_line_item = _insert_params_for(conn, LINE_ITEMS_TABLE_NAME)[0]
        self.assertEqual(first_line_item[0], "parser-test")
        self.assertEqual(first_line_item[1], row["document_id"])
        self.assertEqual(first_line_item[2], 1)
        self.assertEqual(first_line_item[6], row["line_items"][0]["name"])

    def test_resolve_database_url_uses_explicit_env_then_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env_path = Path(temp_dir) / ".env"
            env_path.write_text("DATABASE_URL=postgresql://from-file\n", encoding="utf-8")

            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(
                    resolve_database_url(None, env_path=env_path),
                    "postgresql://from-file",
                )
            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://from-env"}, clear=True):
                self.assertEqual(
                    resolve_database_url(None, env_path=env_path),
                    "postgresql://from-env",
                )
            with patch.dict(os.environ, {"DATABASE_URL": "postgresql://from-env"}, clear=True):
                self.assertEqual(
                    resolve_database_url("postgresql://explicit", env_path=env_path),
                    "postgresql://explicit",
                )


if __name__ == "__main__":
    unittest.main()
