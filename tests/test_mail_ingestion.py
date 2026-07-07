from __future__ import annotations

import base64
import tempfile
import unittest

from app.mail_ingestion import MailIngestionService
from app.mail_store import PdfStorage


class FakeRepo:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.pdfs: list[object] = []
        self.attachments: list[dict[str, object]] = []
        self.jobs: list[dict[str, object]] = []

    def get_account(self, account_id: int) -> dict[str, object]:
        return {
            "id": account_id,
            "provider": "gmail",
            "status": "active",
            "email": "ap@example.com",
        }

    def upsert_message(self, **kwargs: object) -> dict[str, object]:
        self.messages.append(kwargs)
        return {"id": 10, **kwargs}

    def upsert_pdf_file(self, stored_pdf: object) -> dict[str, object]:
        self.pdfs.append(stored_pdf)
        return {"id": 20}

    def upsert_attachment(self, **kwargs: object) -> dict[str, object]:
        self.attachments.append(kwargs)
        return {"id": 30, **kwargs}

    def enqueue_job(self, **kwargs: object) -> bool:
        self.jobs.append(kwargs)
        return True


class FakeTokenManager:
    def access_token_for(self, account_id: int) -> str:
        return "access-token"


class FakeGmail:
    def __init__(self, pdf_content: bytes) -> None:
        self.encoded_pdf = base64.urlsafe_b64encode(pdf_content).decode("ascii").rstrip("=")

    def message(self, *, access_token: str, message_id: str) -> dict[str, object]:
        return {
            "id": message_id,
            "threadId": "thread-1",
            "internalDate": "1710000000000",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "Subject", "value": "Invoice for July"},
                    {"name": "From", "value": "billing@example.com"},
                ],
                "parts": [
                    {
                        "mimeType": "multipart/mixed",
                        "parts": [
                            {
                                "filename": "invoice.pdf",
                                "mimeType": "application/pdf",
                                "body": {"attachmentId": "attachment-1", "size": 12},
                            }
                        ],
                    }
                ],
            },
        }

    def attachment(self, *, access_token: str, message_id: str, attachment_id: str) -> dict[str, str]:
        return {"data": self.encoded_pdf}


class GmailIngestionTests(unittest.TestCase):
    def test_gmail_pdf_attachment_is_saved_and_parse_job_is_enqueued(self) -> None:
        repo = FakeRepo()
        pdf_content = b"%PDF-1.4\ninvoice"

        with tempfile.TemporaryDirectory() as tmp:
            service = MailIngestionService(
                repo=repo,  # type: ignore[arg-type]
                storage=PdfStorage(tmp),
                token_manager=FakeTokenManager(),  # type: ignore[arg-type]
                gmail=FakeGmail(pdf_content),  # type: ignore[arg-type]
                outlook=object(),  # type: ignore[arg-type]
            )

            service.process_gmail_message(account_id=1, message_id="message-1")

        self.assertEqual(repo.messages[0]["subject"], "Invoice for July")
        self.assertEqual(repo.attachments[0]["provider_attachment_id"], "attachment-1")
        self.assertEqual(repo.attachments[0]["candidate_reason"], "invoice_hint")
        self.assertEqual(repo.jobs[0]["job_type"], "parse_pdf")
        self.assertEqual(repo.jobs[0]["unique_key"], "parse-pdf:30")
