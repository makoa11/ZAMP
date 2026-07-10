from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.mail_worker import _handle_job, _storage_pdf_path


class TestRepo:
    def __init__(self) -> None:
        self.parse_results: list[dict[str, object]] = []
        self.completed: list[int] = []
        self.retries: list[dict[str, object]] = []

    def upsert_pdf_parse_result(self, **kwargs: object) -> dict[str, object]:
        self.parse_results.append(kwargs)
        return {"pdf_file_id": kwargs["pdf_file_id"], **kwargs}

    def complete_job(self, *, job_id: int) -> None:
        self.completed.append(job_id)

    def retry_job(self, *, job_id: int, attempts: int, error: str) -> None:
        self.retries.append({"job_id": job_id, "attempts": attempts, "error": error})


class TestIntegration:
    def __init__(self, root: Path) -> None:
        self.repo = TestRepo()
        self.storage = SimpleNamespace(root=root)


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

        parser.assert_called_once_with(b"%PDF-1.4\nfixture", source_id="mail_pdf_file:42")
        self.assertEqual(integration.repo.completed, [99])
        self.assertEqual(integration.repo.retries, [])
        self.assertEqual(integration.repo.parse_results[0]["pdf_file_id"], 42)
        self.assertEqual(integration.repo.parse_results[0]["status"], "parsed")
        self.assertEqual(integration.repo.parse_results[0]["warnings"], ["missing tax"])

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
