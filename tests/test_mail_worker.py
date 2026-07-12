from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.mail_worker import (
    _claim_prioritized_jobs,
    _handle_job,
    _parser_revision,
    _renew_subscriptions_safely,
    _storage_pdf_path,
)


class TestRepo:
    def __init__(self) -> None:
        self.parse_results: list[dict[str, object]] = []
        self.extractions: list[dict[str, object]] = []
        self.decisions: list[dict[str, object]] = []
        self.completed: list[int] = []
        self.retries: list[dict[str, object]] = []
        self.ap_context_record: dict[str, object] | None = None

    def upsert_pdf_parse_result(self, **kwargs: object) -> dict[str, object]:
        self.parse_results.append(kwargs)
        return {"pdf_file_id": kwargs["pdf_file_id"], **kwargs}

    def upsert_mail_invoice_extraction(self, **kwargs: object) -> dict[str, object]:
        self.extractions.append(kwargs)
        return {"pdf_file_id": kwargs["pdf_file_id"], **kwargs}

    def upsert_mail_invoice_decision(self, **kwargs: object) -> dict[str, object]:
        self.decisions.append(kwargs)
        return {"pdf_file_id": kwargs["pdf_file_id"], **kwargs}

    def find_ap_context_record(self, **kwargs: object) -> dict[str, object] | None:
        return self.ap_context_record

    def complete_job(self, *, job_id: int) -> None:
        self.completed.append(job_id)

    def retry_job(self, *, job_id: int, attempts: int, error: str) -> None:
        self.retries.append({"job_id": job_id, "attempts": attempts, "error": error})


class TestIntegration:
    def __init__(self, root: Path) -> None:
        self.config = SimpleNamespace(
            mail_parse_ocr_max_regions=8,
            mail_parse_ocr_max_document_pages=3,
        )
        self.repo = TestRepo()
        self.storage = SimpleNamespace(root=root)


class MailWorkerRenewalTests(unittest.TestCase):
    def test_scheduled_renewal_exception_is_contained(self) -> None:
        integration = SimpleNamespace(
            ingestion=SimpleNamespace(
                renew_mail_subscriptions=lambda: (_ for _ in ()).throw(RuntimeError("database down"))
            )
        )

        self.assertFalse(_renew_subscriptions_safely(integration))  # type: ignore[arg-type]

    def test_parser_revision_records_unlimited_document_ocr(self) -> None:
        integration = SimpleNamespace(
            config=SimpleNamespace(
                mail_parse_ocr_max_regions=8,
                mail_parse_ocr_max_document_pages=None,
            )
        )

        self.assertIn("ocr-pages=all", _parser_revision(integration))


class ClaimPrioritizedJobsTests(unittest.TestCase):
    def test_mail_fetch_jobs_are_claimed_before_parse_jobs(self) -> None:
        calls: list[dict[str, object]] = []

        class Repo:
            def claim_jobs(self, **kwargs: object) -> list[dict[str, object]]:
                calls.append(kwargs)
                job_types = set(kwargs["job_types"])  # type: ignore[arg-type]
                if "parse_pdf" in job_types:
                    return [{"id": 2, "type": "parse_pdf"}]
                return [{"id": 1, "type": "gmail_message_fetch"}]

        integration = SimpleNamespace(repo=Repo())

        jobs = _claim_prioritized_jobs(integration, worker_id="worker-1", limit=2)  # type: ignore[arg-type]

        self.assertEqual([job["type"] for job in jobs], ["gmail_message_fetch", "parse_pdf"])
        self.assertNotIn("parse_pdf", set(calls[0]["job_types"]))  # type: ignore[arg-type]
        self.assertEqual(set(calls[1]["job_types"]), {"parse_pdf"})  # type: ignore[arg-type]


def _field(value: object, *, confidence: float = 0.95) -> dict[str, object]:
    return {
        "raw": value,
        "value": value,
        "page": 1,
        "bbox": [10.0, 20.0, 80.0, 28.0],
        "label": "test",
        "confidence": confidence,
        "method": "unit_test",
    }


def _money(value: object, *, confidence: float = 0.95) -> dict[str, object]:
    return {
        "raw": f"USD {value}",
        "value": float(value),
        "amount": float(value),
        "currency": "USD",
        "page": 1,
        "bbox": [120.0, 160.0, 180.0, 168.0],
        "label": "balance due",
        "confidence": confidence,
        "method": "unit_test",
    }


def _parsed_invoice_result() -> dict[str, object]:
    return {
        "status": "parsed",
        "parser_version": "static-pdf-v1",
        "fields": {
            "invoice_number": _field("INV-1045"),
            "issue_date": _field("2026-07-01"),
            "due_date": _field("2026-07-31"),
            "purchase_order": _field("PO-1000"),
            "terms": _field("Net 30"),
            "currency": _field("USD"),
            "seller": _field("Acme Supplies LLC\nbilling@acme.example"),
            "buyer": _field("Beta Foods Inc"),
            "subtotal": _money("1000.00"),
            "discount": _money("0.00"),
            "tax": _money("0.00"),
            "shipping": _money("0.00"),
            "paid": _money("0.00"),
            "balance_due": _money("1000.00"),
            "payment_instructions": _field("ACH transfer **** 1234\nbilling@acme.example"),
            "line_items": [],
        },
        "pages": [{"page": 1, "text": "invoice"}],
        "warnings": [],
    }


def _approve_ap_context_record() -> dict[str, object]:
    return {
        "id": 1,
        "source_key": "unit:approve",
        "_match_strategy": "vendor_po",
        "source_metadata": {"source": "unit_test"},
        "context": {
            "schema_version": 1,
            "available": True,
            "source": {"type": "unit_test"},
            "scenario": "unit_approve",
            "vendor": {
                "name": "Acme Supplies LLC",
                "normalized_name": "acme supplies",
                "approved": True,
            },
            "purchase_order": {
                "po_number": "PO-1000",
                "normalized": "PO1000",
                "authorized_total": "1000.00",
                "previously_consumed": "0.00",
                "remaining_before_invoice": "1000.00",
            },
            "invoice_total": "1000.00",
            "approved_bank_details": {"account": "**** 1234"},
            "invoice_payment": {"bank_account": "**** 1234"},
            "previous_invoices": [],
            "duplicate_candidates": [],
            "candidate_open_po": None,
            "tolerance_policy": {"percent": "0.00", "amount": "0.00"},
            "expected": {},
        },
    }


class MailWorkerParsePdfTests(unittest.TestCase):
    def test_parse_pdf_job_reads_storage_upserts_result_and_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "invoice.pdf").write_bytes(b"%PDF-1.4\nfixture")
            integration = TestIntegration(root)
            job = {
                "id": 99,
                "type": "parse_pdf",
                "attempts": 1,
                "payload": json.dumps({"pdf_file_id": 42, "storage_path": "invoice.pdf"}),
            }
            parser_result = {
                "status": "parsed",
                "parser_version": "static-pdf-v1",
                "fields": {"line_items": []},
                "pages": [],
                "warnings": ["missing tax"],
            }

            with patch("app.mail_worker.parse_invoice_pdf", return_value=parser_result) as parser:
                _handle_job(integration, job)  # type: ignore[arg-type]

        parser.assert_called_once_with(
            b"%PDF-1.4\nfixture",
            source_id="mail_pdf_file:42",
            ocr_max_regions=8,
            ocr_max_document_pages=3,
        )
        self.assertEqual(integration.repo.completed, [99])
        self.assertEqual(integration.repo.retries, [])
        self.assertEqual(integration.repo.parse_results[0]["pdf_file_id"], 42)
        self.assertEqual(integration.repo.parse_results[0]["status"], "parsed")
        self.assertEqual(integration.repo.parse_results[0]["warnings"], ["missing tax"])

    def test_parse_pdf_job_persists_normalized_extraction_and_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "invoice.pdf").write_bytes(b"%PDF-1.4\nfixture")
            integration = TestIntegration(root)
            integration.repo.ap_context_record = _approve_ap_context_record()
            job = {
                "id": 101,
                "type": "parse_pdf",
                "attempts": 1,
                "payload": {
                    "attachment_id": 7,
                    "pdf_file_id": 42,
                    "storage_path": "invoice.pdf",
                    "owner_user_id": "user-123",
                },
            }

            with patch("app.mail_worker.parse_invoice_pdf", return_value=_parsed_invoice_result()):
                _handle_job(integration, job)  # type: ignore[arg-type]

        self.assertEqual(integration.repo.completed, [101])
        self.assertEqual(integration.repo.extractions[0]["owner_user_id"], "user-123")
        self.assertEqual(integration.repo.extractions[0]["attachment_id"], 7)
        normalized = integration.repo.extractions[0]["normalized_invoice"]
        self.assertIsInstance(normalized, dict)
        self.assertEqual(normalized["vendor"]["normalized_name"], "acme supplies")
        decision = integration.repo.decisions[0]["decision_result"]
        self.assertIsInstance(decision, dict)
        self.assertEqual(decision["decision"], "approve")
        self.assertEqual(
            decision["audit"]["ap_context_summary"]["source"]["type"],
            "ap_context_records",
        )

    def test_parse_pdf_job_without_ap_context_routes_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "invoice.pdf").write_bytes(b"%PDF-1.4\nfixture")
            integration = TestIntegration(root)
            job = {
                "id": 102,
                "type": "parse_pdf",
                "attempts": 1,
                "payload": {
                    "attachment_id": 8,
                    "pdf_file_id": 43,
                    "storage_path": "invoice.pdf",
                    "owner_user_id": "user-123",
                },
            }

            with patch("app.mail_worker.parse_invoice_pdf", return_value=_parsed_invoice_result()):
                _handle_job(integration, job)  # type: ignore[arg-type]

        decision = integration.repo.decisions[0]["decision_result"]
        self.assertIsInstance(decision, dict)
        self.assertEqual(decision["decision"], "needs_review")
        self.assertEqual(decision["audit"]["ap_context_summary"]["available"], False)

    def test_parse_pdf_job_passes_ocr_region_cap_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "invoice.pdf").write_bytes(b"%PDF-1.4\nfixture")
            integration = TestIntegration(root)
            integration.config.mail_parse_ocr_max_regions = 3
            job = {
                "id": 99,
                "type": "parse_pdf",
                "attempts": 1,
                "payload": json.dumps({"pdf_file_id": 42, "storage_path": "invoice.pdf"}),
            }
            parser_result = {
                "status": "parsed",
                "parser_version": "static-pdf-v2",
                "fields": {"line_items": []},
                "pages": [],
                "warnings": [],
            }

            with patch("app.mail_worker.parse_invoice_pdf", return_value=parser_result) as parser:
                _handle_job(integration, job)  # type: ignore[arg-type]

        parser.assert_called_once_with(
            b"%PDF-1.4\nfixture",
            source_id="mail_pdf_file:42",
            ocr_max_regions=3,
            ocr_max_document_pages=3,
        )
        self.assertEqual(integration.repo.completed, [99])

    def test_parse_pdf_job_retries_when_parser_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            Path(root, "invoice.pdf").write_bytes(b"%PDF-1.4\nfixture")
            integration = TestIntegration(root)
            job = {
                "id": 100,
                "type": "parse_pdf",
                "attempts": 3,
                "payload": {"pdf_file_id": 43, "storage_path": "invoice.pdf"},
            }

            with patch("app.mail_worker.parse_invoice_pdf", side_effect=RuntimeError("parser exploded")):
                _handle_job(integration, job)  # type: ignore[arg-type]

        self.assertEqual(integration.repo.completed, [])
        self.assertEqual(integration.repo.parse_results, [])
        self.assertEqual(integration.repo.retries[0]["job_id"], 100)
        self.assertEqual(integration.repo.retries[0]["attempts"], 3)
        self.assertIn("parser exploded", str(integration.repo.retries[0]["error"]))

    def test_storage_pdf_path_rejects_paths_outside_storage_root(self) -> None:
        with self.assertRaises(RuntimeError):
            _storage_pdf_path("/tmp/mail-pdfs", "../invoice.pdf")
        with self.assertRaises(RuntimeError):
            _storage_pdf_path("/tmp/mail-pdfs", "/tmp/invoice.pdf")


if __name__ == "__main__":
    unittest.main()
