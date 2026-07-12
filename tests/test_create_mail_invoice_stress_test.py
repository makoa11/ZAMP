from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.create_mail_invoice_stress_test import (
    generate_mail_invoice_stress_pdfs,
    resolve_owner_user_id,
    seed_mail_invoice_ap_context,
)


class GenerateMailInvoiceStressPdfsTests(unittest.TestCase):
    def test_generates_ten_single_document_stress_pdfs_and_manifests(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = generate_mail_invoice_stress_pdfs(
                output_dir=root,
                seed=500,
                today=date(2026, 7, 13),
            )

            self.assertEqual(len(paths), 10)
            self.assertEqual(len(list(root.glob("*.pdf"))), 10)
            self.assertEqual(len(list(root.glob("*.manifest.json"))), 10)
            self.assertEqual(
                [path.name for path in paths[:3]],
                [
                    "mail-invoice-stress-01-multipage-continuation.pdf",
                    "mail-invoice-stress-02-ambiguous-labels-mixed-totals.pdf",
                    "mail-invoice-stress-03-currency-glyphs.pdf",
                ],
            )
            for path in paths:
                manifest = json.loads(path.with_suffix(".manifest.json").read_text(encoding="utf-8"))
                self.assertEqual(manifest["suite"], "mail_stress")
                self.assertEqual(len(manifest["documents"]), 1)
                self.assertTrue(path.read_bytes().startswith(b"%PDF-1.4"))

            multipage = json.loads(paths[0].with_suffix(".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(multipage["pdf"]["page_count"], 3)
            self.assertEqual(len(multipage["documents"][0]["pages"]), 3)

    def test_requires_positive_count_and_seed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ValueError, "pdf-count must be at least 1"):
                generate_mail_invoice_stress_pdfs(output_dir=Path(temp_dir), pdf_count=0)
            with self.assertRaisesRegex(ValueError, "seed must be greater than 0"):
                generate_mail_invoice_stress_pdfs(output_dir=Path(temp_dir), seed=0)


class SeedMailInvoiceApContextTests(unittest.TestCase):
    def test_seeds_one_owned_ap_context_record_per_pdf(self) -> None:
        class Repo:
            def __init__(self) -> None:
                self.records: list[dict[str, object]] = []

            def upsert_ap_context_record(self, **record: object) -> None:
                self.records.append(record)

        with tempfile.TemporaryDirectory() as temp_dir:
            paths = generate_mail_invoice_stress_pdfs(
                output_dir=Path(temp_dir),
                pdf_count=3,
                seed=500,
                today=date(2026, 7, 13),
            )
            repo = Repo()
            count = seed_mail_invoice_ap_context(
                repo,
                pdf_paths=paths,
                owner_user_id="user-123",
            )

        self.assertEqual(count, 3)
        self.assertEqual(len(repo.records), 3)
        self.assertTrue(all(record["owner_user_id"] == "user-123" for record in repo.records))
        self.assertTrue(all(record["normalized_vendor"] for record in repo.records))
        self.assertTrue(all(record["normalized_invoice_number"] for record in repo.records))


class ResolveOwnerUserIdTests(unittest.TestCase):
    def test_uses_explicit_owner_without_querying_database(self) -> None:
        class Database:
            def connect(self) -> None:
                raise AssertionError("database should not be queried")

        self.assertEqual(resolve_owner_user_id(Database(), " user-123 "), "user-123")

    def test_auto_detects_only_connected_mailbox_owner(self) -> None:
        class Result:
            def fetchall(self) -> list[dict[str, str]]:
                return [{"owner_user_id": "user-123"}]

        class Connection:
            def __enter__(self) -> "Connection":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def execute(self, query: str) -> Result:
                self.query = query
                return Result()

        class Database:
            def connect(self) -> Connection:
                return Connection()

        self.assertEqual(resolve_owner_user_id(Database(), None), "user-123")

    def test_requires_explicit_owner_when_multiple_are_connected(self) -> None:
        class Result:
            def fetchall(self) -> list[tuple[str]]:
                return [("user-1",), ("user-2",)]

        class Connection:
            def __enter__(self) -> "Connection":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def execute(self, query: str) -> Result:
                return Result()

        class Database:
            def connect(self) -> Connection:
                return Connection()

        with self.assertRaisesRegex(RuntimeError, "Multiple connected mailbox owners"):
            resolve_owner_user_id(Database(), None)


if __name__ == "__main__":
    unittest.main()
