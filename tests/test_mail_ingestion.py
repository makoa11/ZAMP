from __future__ import annotations

import base64
import tempfile
import unittest

from app.mail_ingestion import (
    GMAIL_PDF_FALLBACK_QUERY,
    MailIngestionService,
    compile_invoice_match_regexes,
    is_invoice_candidate,
    suggest_invoice_match_pattern_from_filename,
)
from app.mail_store import PdfStorage

INVOICE_PATTERNS = [r"\binvoice\b", r"^inv(?:[\s._:#-]+[a-z0-9-]+|[0-9][a-z0-9-]*)\b"]


class TestRepo:
    def __init__(self, *, provider: str = "gmail", patterns: list[str] | None = None) -> None:
        self.provider = provider
        self.patterns = patterns or []
        self.messages: list[dict[str, object]] = []
        self.pdfs: list[object] = []
        self.attachments: list[dict[str, object]] = []
        self.jobs: list[dict[str, object]] = []

    def get_account(self, account_id: int) -> dict[str, object]:
        return {
            "id": account_id,
            "provider": self.provider,
            "status": "active",
            "email": "ap@example.com",
            "owner_user_id": "user-123",
        }

    def get_invoice_match_patterns(self, *, owner_user_id: str) -> list[str]:
        return self.patterns

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


class TestTokenManager:
    def access_token_for(self, account_id: int) -> str:
        return "access-token"


class TestGmail:
    def __init__(
        self,
        pdf_content: bytes,
        *,
        subject: str = "Invoice for July",
        filename: str = "invoice.pdf",
        snippet: str = "",
        body_text: str = "",
    ) -> None:
        self.encoded_pdf = base64.urlsafe_b64encode(pdf_content).decode("ascii").rstrip("=")
        self.subject = subject
        self.filename = filename
        self.snippet = snippet
        self.encoded_body = base64.urlsafe_b64encode(body_text.encode("utf-8")).decode("ascii").rstrip("=")
        self.attachment_calls = 0
        self.message_calls = 0
        self.list_queries: list[str] = []

    def list_messages(
        self,
        *,
        access_token: str,
        query: str,
        page_token: str | None = None,
    ) -> dict[str, object]:
        self.list_queries.append(query)
        return {"messages": []}

    def message(self, *, access_token: str, message_id: str) -> dict[str, object]:
        self.message_calls += 1
        parts: list[dict[str, object]] = []
        if self.encoded_body:
            parts.append(
                {
                    "mimeType": "text/plain",
                    "body": {"data": self.encoded_body},
                }
            )
        parts.append(
            {
                "filename": self.filename,
                "mimeType": "application/pdf",
                "body": {"attachmentId": "attachment-1", "size": 12},
            }
        )
        return {
            "id": message_id,
            "threadId": "thread-1",
            "internalDate": "1710000000000",
            "labelIds": ["INBOX"],
            "snippet": self.snippet,
            "payload": {
                "headers": [
                    {"name": "Subject", "value": self.subject},
                    {"name": "From", "value": "billing@example.com"},
                ],
                "parts": [
                    {
                        "mimeType": "multipart/mixed",
                        "parts": parts,
                    }
                ],
            },
        }

    def attachment(self, *, access_token: str, message_id: str, attachment_id: str) -> dict[str, str]:
        self.attachment_calls += 1
        return {"data": self.encoded_pdf}


class TestOutlook:
    def __init__(
        self,
        pdf_content: bytes,
        *,
        subject: str = "Invoice for July",
        filename: str = "invoice.pdf",
        body_preview: str = "",
    ) -> None:
        self.encoded_pdf = base64.b64encode(pdf_content).decode("ascii")
        self.subject = subject
        self.filename = filename
        self.body_preview = body_preview
        self.attachment_calls = 0
        self.message_calls = 0

    def message(self, *, access_token: str, message_id: str) -> dict[str, object]:
        self.message_calls += 1
        return {
            "id": message_id,
            "subject": self.subject,
            "bodyPreview": self.body_preview,
            "receivedDateTime": "2024-03-09T16:00:00Z",
            "hasAttachments": True,
            "conversationId": "conversation-1",
            "internetMessageId": "<message-1@example.com>",
            "from": {"emailAddress": {"address": "billing@example.com"}},
        }

    def attachments(self, *, access_token: str, message_id: str) -> list[dict[str, object]]:
        self.attachment_calls += 1
        return [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "id": "attachment-1",
                "name": self.filename,
                "contentType": "application/pdf",
                "isInline": False,
                "size": 12,
                "contentBytes": self.encoded_pdf,
            }
        ]


class InvoiceCandidateTests(unittest.TestCase):
    def test_filename_helper_generates_regex_from_sample_invoice_name(self) -> None:
        pattern = suggest_invoice_match_pattern_from_filename("INV-2024-001.pdf")
        regexes = compile_invoice_match_regexes([pattern])

        self.assertEqual(pattern, r"^INV[\s._:#-]*\d+[\s._:#-]*\d+(?:\.pdf)?$")
        self.assertTrue(
            is_invoice_candidate(
                subject=None,
                filename="INV_2025_002.pdf",
                invoice_match_regexes=regexes,
            )
        )
        self.assertFalse(
            is_invoice_candidate(
                subject=None,
                filename="receipt-2025.pdf",
                invoice_match_regexes=regexes,
            )
        )

    def test_filename_helper_uses_basename_and_escapes_literal_text(self) -> None:
        pattern = suggest_invoice_match_pattern_from_filename(r"C:\Downloads\ACME.Co Invoice #123.pdf")
        regexes = compile_invoice_match_regexes([pattern])

        self.assertTrue(
            is_invoice_candidate(
                subject=None,
                filename="ACME.Co Invoice 456.pdf",
                invoice_match_regexes=regexes,
            )
        )
        self.assertFalse(
            is_invoice_candidate(
                subject=None,
                filename="Other.Co Invoice 456.pdf",
                invoice_match_regexes=regexes,
            )
        )

    def test_invoice_candidate_matches_inv_filename_without_matching_invitation(self) -> None:
        regexes = compile_invoice_match_regexes(INVOICE_PATTERNS)
        self.assertTrue(
            is_invoice_candidate(
                subject=None,
                filename="INV-2024-001.pdf",
                invoice_match_regexes=regexes,
            )
        )
        self.assertFalse(
            is_invoice_candidate(
                subject="Invitation documents",
                filename="passport.pdf",
                body="personal travel file",
                invoice_match_regexes=regexes,
            )
        )


class GmailIngestionTests(unittest.TestCase):
    def test_gmail_fallback_uses_pdf_query_without_user_patterns(self) -> None:
        repo = TestRepo(patterns=[])
        gmail = TestGmail(b"%PDF-1.4\ninvoice")

        service = MailIngestionService(
            repo=repo,  # type: ignore[arg-type]
            storage=PdfStorage("/tmp"),
            token_manager=TestTokenManager(),  # type: ignore[arg-type]
            gmail=gmail,  # type: ignore[arg-type]
            outlook=object(),  # type: ignore[arg-type]
        )

        service.process_gmail_fallback(account_id=1)

        self.assertEqual(gmail.list_queries, [GMAIL_PDF_FALLBACK_QUERY])

    def test_gmail_fallback_uses_pdf_query_after_user_patterns_are_saved(self) -> None:
        repo = TestRepo(patterns=INVOICE_PATTERNS)
        gmail = TestGmail(b"%PDF-1.4\ninvoice")

        service = MailIngestionService(
            repo=repo,  # type: ignore[arg-type]
            storage=PdfStorage("/tmp"),
            token_manager=TestTokenManager(),  # type: ignore[arg-type]
            gmail=gmail,  # type: ignore[arg-type]
            outlook=object(),  # type: ignore[arg-type]
        )

        service.process_gmail_fallback(account_id=1)

        self.assertEqual(gmail.list_queries, [GMAIL_PDF_FALLBACK_QUERY])

    def test_gmail_pdf_attachment_is_saved_and_parse_job_is_enqueued(self) -> None:
        repo = TestRepo(patterns=INVOICE_PATTERNS)
        pdf_content = b"%PDF-1.4\ninvoice"
        gmail = TestGmail(pdf_content)

        with tempfile.TemporaryDirectory() as tmp:
            service = MailIngestionService(
                repo=repo,  # type: ignore[arg-type]
                storage=PdfStorage(tmp),
                token_manager=TestTokenManager(),  # type: ignore[arg-type]
                gmail=gmail,  # type: ignore[arg-type]
                outlook=object(),  # type: ignore[arg-type]
            )

            service.process_gmail_message(account_id=1, message_id="message-1")

        self.assertEqual(repo.messages[0]["subject"], "Invoice for July")
        self.assertEqual(repo.attachments[0]["provider_attachment_id"], "attachment-1")
        self.assertEqual(repo.attachments[0]["candidate_reason"], "invoice_hint")
        self.assertEqual(repo.jobs[0]["job_type"], "parse_pdf")
        self.assertEqual(repo.jobs[0]["unique_key"], "parse-pdf:30")
        self.assertEqual(gmail.attachment_calls, 1)

    def test_gmail_pdf_attachment_is_saved_without_user_patterns(self) -> None:
        repo = TestRepo(patterns=[])
        pdf_content = b"%PDF-1.4\ninvoice"
        gmail = TestGmail(pdf_content)

        with tempfile.TemporaryDirectory() as tmp:
            service = MailIngestionService(
                repo=repo,  # type: ignore[arg-type]
                storage=PdfStorage(tmp),
                token_manager=TestTokenManager(),  # type: ignore[arg-type]
                gmail=gmail,  # type: ignore[arg-type]
                outlook=object(),  # type: ignore[arg-type]
            )

            service.process_gmail_message(account_id=1, message_id="message-1")

        self.assertEqual(gmail.message_calls, 1)
        self.assertEqual(gmail.attachment_calls, 1)
        self.assertEqual(len(repo.pdfs), 1)
        self.assertEqual(repo.attachments[0]["candidate_reason"], "pdf_attachment")

    def test_gmail_non_invoice_pdf_is_ignored_before_attachment_fetch(self) -> None:
        repo = TestRepo(patterns=INVOICE_PATTERNS)
        pdf_content = b"%PDF-1.4\npassport"
        gmail = TestGmail(
            pdf_content,
            subject="Travel documents",
            filename="passport.pdf",
            snippet="personal file attached",
            body_text="Here is the document you asked for.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            service = MailIngestionService(
                repo=repo,  # type: ignore[arg-type]
                storage=PdfStorage(tmp),
                token_manager=TestTokenManager(),  # type: ignore[arg-type]
                gmail=gmail,  # type: ignore[arg-type]
                outlook=object(),  # type: ignore[arg-type]
            )

            service.process_gmail_message(account_id=1, message_id="message-1")

        self.assertEqual(repo.messages, [])
        self.assertEqual(repo.pdfs, [])
        self.assertEqual(repo.attachments, [])
        self.assertEqual(repo.jobs, [])
        self.assertEqual(gmail.attachment_calls, 0)

    def test_gmail_body_invoice_hint_saves_pdf(self) -> None:
        repo = TestRepo(patterns=INVOICE_PATTERNS)
        pdf_content = b"%PDF-1.4\ninvoice"
        gmail = TestGmail(
            pdf_content,
            subject="Monthly documents",
            filename="statement.pdf",
            body_text="Please process the invoice attached.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            service = MailIngestionService(
                repo=repo,  # type: ignore[arg-type]
                storage=PdfStorage(tmp),
                token_manager=TestTokenManager(),  # type: ignore[arg-type]
                gmail=gmail,  # type: ignore[arg-type]
                outlook=object(),  # type: ignore[arg-type]
            )

            service.process_gmail_message(account_id=1, message_id="message-1")

        self.assertEqual(len(repo.pdfs), 1)
        self.assertEqual(repo.attachments[0]["candidate_reason"], "invoice_hint")


class OutlookIngestionTests(unittest.TestCase):
    def test_outlook_non_invoice_pdf_is_not_saved(self) -> None:
        repo = TestRepo(provider="outlook", patterns=INVOICE_PATTERNS)
        pdf_content = b"%PDF-1.4\npassport"
        outlook = TestOutlook(
            pdf_content,
            subject="Travel documents",
            filename="passport.pdf",
            body_preview="Here is the document you asked for.",
        )

        with tempfile.TemporaryDirectory() as tmp:
            service = MailIngestionService(
                repo=repo,  # type: ignore[arg-type]
                storage=PdfStorage(tmp),
                token_manager=TestTokenManager(),  # type: ignore[arg-type]
                gmail=object(),  # type: ignore[arg-type]
                outlook=outlook,  # type: ignore[arg-type]
            )

            service.process_outlook_message(account_id=1, message_id="message-1")

        self.assertEqual(repo.messages, [])
        self.assertEqual(repo.pdfs, [])
        self.assertEqual(repo.attachments, [])
        self.assertEqual(repo.jobs, [])
        self.assertEqual(outlook.attachment_calls, 1)

    def test_outlook_pdf_attachment_is_saved_without_user_patterns(self) -> None:
        repo = TestRepo(provider="outlook", patterns=[])
        pdf_content = b"%PDF-1.4\ninvoice"
        outlook = TestOutlook(pdf_content)

        with tempfile.TemporaryDirectory() as tmp:
            service = MailIngestionService(
                repo=repo,  # type: ignore[arg-type]
                storage=PdfStorage(tmp),
                token_manager=TestTokenManager(),  # type: ignore[arg-type]
                gmail=object(),  # type: ignore[arg-type]
                outlook=outlook,  # type: ignore[arg-type]
            )

            service.process_outlook_message(account_id=1, message_id="message-1")

        self.assertEqual(outlook.message_calls, 1)
        self.assertEqual(outlook.attachment_calls, 1)
        self.assertEqual(len(repo.pdfs), 1)
        self.assertEqual(repo.attachments[0]["candidate_reason"], "pdf_attachment")
