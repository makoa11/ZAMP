from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.server import ZampHTTPServer, ZampRequestHandler, _filter_invoice_items
from app.templates import login_page, signup_page


class TestSocket:
    def __init__(self, request: bytes, fail_on_send_number: int | None = None) -> None:
        self.reader = io.BytesIO(request)
        self.writer = io.BytesIO()
        self.fail_on_send_number = fail_on_send_number
        self.send_count = 0

    def makefile(self, mode: str, buffering: int | None = None) -> io.BytesIO:
        if "r" in mode:
            return self.reader
        return self.writer

    def sendall(self, data: bytes) -> None:
        self.send_count += 1
        if self.fail_on_send_number == self.send_count:
            raise BrokenPipeError("client disconnected")
        self.writer.write(data)


class ServerRouteTests(unittest.TestCase):
    def test_auth_pages_use_refreshed_split_layout(self) -> None:
        login_html = login_page(csrf_token="csrf")
        signup_html = signup_page(csrf_token="csrf")

        self.assertIn('class="auth-shell auth-shell-split"', login_html)
        self.assertIn('class="auth-layout"', login_html)
        self.assertIn("Review invoices with structure, context, and clear next steps.", login_html)
        self.assertIn('class="auth-switch-chip" href="/signup"', login_html)

        self.assertIn('class="auth-shell auth-shell-split"', signup_html)
        self.assertIn("Set up an account that fits the review workflow from day one.", signup_html)
        self.assertIn('class="auth-switch-chip" href="/login"', signup_html)

    def test_client_disconnect_during_response_is_ignored(self) -> None:
        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        request = b"GET /logout HTTP/1.1\r\nHost: localhost\r\n\r\n"
        fake_socket = TestSocket(request, fail_on_send_number=2)

        Handler(fake_socket, ("127.0.0.1", 12345), SimpleNamespace())

        self.assertEqual(fake_socket.send_count, 2)

    def test_server_close_closes_mail_integration(self) -> None:
        class MailIntegration:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        mail = MailIntegration()
        server = ZampHTTPServer.__new__(ZampHTTPServer)
        server.mail_integration = mail  # type: ignore[assignment]

        with patch.object(ThreadingHTTPServer, "server_close") as close_socket:
            ZampHTTPServer.server_close(server)

        close_socket.assert_called_once()
        self.assertTrue(mail.closed)

    def test_get_logout_is_non_mutating(self) -> None:
        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        request = (
            b"GET /logout HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Cookie: zamp_session=sealed\r\n"
            b"\r\n"
        )
        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue().decode("iso-8859-1")

        self.assertIn(" 405 ", response.splitlines()[0])
        self.assertIn("Allow: POST\r\n", response)
        self.assertNotIn("Set-Cookie:", response)

    def test_invoice_samples_api_returns_generated_models(self) -> None:
        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        request = (
            b"GET /api/invoices/samples?paper=a4&count=2&seed=10 HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"\r\n"
        )
        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue().decode("iso-8859-1")
        _, body = response.split("\r\n\r\n", 1)
        payload = json.loads(body)

        self.assertIn(" 200 ", response.splitlines()[0])
        self.assertEqual(len(payload["samples"]), 2)
        self.assertEqual(payload["samples"][0]["paper"]["slug"], "a4")
        self.assertIn("components", payload["samples"][0])

    def test_invoice_samples_api_rejects_invalid_count(self) -> None:
        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        request = (
            b"GET /api/invoices/samples?count=100 HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"\r\n"
        )
        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue().decode("iso-8859-1")

        self.assertIn(" 400 ", response.splitlines()[0])

    def test_invoice_samples_pdf_returns_pdf(self) -> None:
        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        request = (
            b"GET /api/invoices/samples.pdf?paper=a4&count=1&seed=10 HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"\r\n"
        )
        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue()
        header, body = response.split(b"\r\n\r\n", 1)

        self.assertIn(b" 200 ", header.splitlines()[0])
        self.assertIn(b"Content-Type: application/pdf", header)
        self.assertTrue(body.startswith(b"%PDF-1.4"))
        self.assertTrue(body.rstrip().endswith(b"%%EOF"))

    def test_dashboard_uses_database_invoice_queue_not_generated_samples(self) -> None:
        class MailIntegration:
            def __init__(self) -> None:
                self.list_owner_user_id: str | None = None
                self.list_limit: int | None = None
                self.list_offset: int | None = None
                self.detail_request: tuple[str, int] | None = None

            def count_invoices(self, *, owner_user_id: str) -> int:
                self.list_owner_user_id = owner_user_id
                return 2

            def list_invoices(
                self, *, owner_user_id: str, limit: int = 100, offset: int = 0
            ) -> list[dict[str, object]]:
                self.list_owner_user_id = owner_user_id
                self.list_limit = limit
                self.list_offset = offset
                return [
                    {
                        "pdf_file_id": 20,
                        "filename": "vendor-invoice.pdf",
                        "invoice_number": "INV-DB-100",
                        "amount_due": "216.00",
                        "currency": "USD",
                        "vendor": "Database Vendor LLC",
                        "subject": "July invoice",
                        "received_date": "2026-07-10T10:00:00+00:00",
                        "decision": "needs_review",
                        "confidence": "medium",
                        "next_action": "Route to AP review.",
                    },
                    {
                        "pdf_file_id": 21,
                        "filename": "accepted-invoice.pdf",
                        "invoice_number": "INV-OK-200",
                        "amount_due": "84.00",
                        "currency": "USD",
                        "vendor": "Auto Approved LLC",
                        "subject": "Approved invoice",
                        "received_date": "2026-07-09T10:00:00+00:00",
                        "decision": "approve",
                        "confidence": "high",
                    },
                ]

            def get_invoice(
                self, *, owner_user_id: str, pdf_file_id: int
            ) -> dict[str, object]:
                self.detail_request = (owner_user_id, pdf_file_id)
                return {
                    "pdf_file_id": pdf_file_id,
                    "message": {
                        "sender": "billing@example.com",
                        "subject": "July invoice",
                        "received_at": "2026-07-10T10:00:00+00:00",
                    },
                    "attachment": {"filename": "vendor-invoice.pdf"},
                    "raw_parse": {
                        "status": "parsed",
                        "parser_method": "static_text",
                        "page_count": 2,
                        "ocr_parts": ["invoice_number", "amount_due"],
                    },
                    "normalized_invoice": {
                        "vendor": {"name": "Database Vendor LLC"},
                        "invoice_number": {"value": "INV-DB-100"},
                        "purchase_order": {"value": "PO-42"},
                        "issue_date": {"value": "2026-07-01"},
                        "amount_due": {"amount": "216.00", "currency": "USD"},
                    },
                    "decision": {
                        "decision": "needs_review",
                        "confidence": "medium",
                        "summary": "Invoice amount is outside tolerance.",
                        "next_action": "Route to AP review for variance approval.",
                    },
                    "checks": [
                        {
                            "id": "amount_match",
                            "status": "fail",
                            "summary": "Amount exceeded configured tolerance.",
                        }
                    ],
                    "audit": {
                        "normalized_vendor": "DATABASE VENDOR LLC",
                        "normalized_invoice_number": "DB100",
                        "purchase_order": "PO42",
                        "amount_due": "216.00",
                    },
                    "ap_context": {
                        "available": True,
                        "reason": "No simulated AP context record matched the parsed vendor, PO, invoice number, amount, or date.",
                        "scenario": "amount_variance",
                        "source": {"type": "ap_context_records", "record_id": 5},
                    },
                }

        mail = MailIntegration()

        class Handler(ZampRequestHandler):
            mail_integration = mail  # type: ignore[assignment]

            def log_message(self, format: str, *args: object) -> None:
                pass

            def _session(self) -> tuple[object, None, list[str]]:
                return object(), None, []

            def _session_payload(self, session: object) -> dict[str, object]:
                return {"user": {"id": "user-123"}}

            def _csrf_cookie(self, cookie_values: dict[str, str]) -> tuple[str, None]:
                return "csrf-token", None

        request = b"GET /dashboard HTTP/1.1\r\nHost: localhost\r\n\r\n"
        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue().decode("iso-8859-1")

        self.assertIn(" 200 ", response.splitlines()[0])
        self.assertEqual(mail.list_owner_user_id, "user-123")
        self.assertEqual(mail.list_limit, 500)
        self.assertEqual(mail.list_offset, 0)
        self.assertEqual(mail.detail_request, ("user-123", 20))
        self.assertIn("INV-DB-100", response)
        self.assertIn("Database Vendor LLC", response)
        self.assertIn("Invoice amount is outside tolerance.", response)
        self.assertIn("Route to accounts payable review for variance approval.", response)
        self.assertIn("Amount match", response)
        self.assertIn("DATABASE VENDOR LLC", response)
        self.assertIn("Accounts payable context", response)
        self.assertIn(
            "No simulated accounts payable context record matched the parsed vendor, purchase order, invoice number, amount due, or invoice date.",
            response,
        )
        self.assertIn("Accounts payable context records", response)
        self.assertNotIn("What matters now", response)
        self.assertNotIn("Invoice in focus", response)
        self.assertNotIn("decision-orb", response)
        self.assertIn("Collapse invoice sidebar", response)
        self.assertIn("zamp-sidebar-collapsed", response)
        self.assertIn("zamp-review-queue-scroll-top", response)
        self.assertIn("zamp-review-queue-scroll-left", response)
        self.assertIn("data-previous-invoice", response)
        self.assertIn("data-next-invoice", response)
        self.assertIn('d="M4 6h16M4 12h16M4 18h16"', response)
        self.assertIn("Your next step", response)
        self.assertIn("Review issue", response)
        self.assertIn('href="#decision-steps"', response)
        self.assertIn("Already done", response)
        self.assertIn("Still to do", response)
        self.assertNotIn("Zen mode", response)
        self.assertIn("Supporting details", response)
        self.assertIn("detail-fact-row is-ocr", response)
        self.assertNotIn("field-source-badge", response)
        self.assertIn("2</strong> processed", response)
        self.assertIn("1</strong> shown", response)
        self.assertIn('name="review" value="needs_review" checked', response)
        self.assertIn('/api/mail/invoices/20/overlay.pdf?boxes=parsed', response)
        self.assertNotIn('/api/mail/pdfs/20', response)
        self.assertNotIn("AP context", response)
        self.assertNotIn("AP review", response)
        self.assertNotIn("ap_context_records", response)
        self.assertIn("Auto Approved LLC", response)
        self.assertIn(
            'data-needs-review="false" data-received="2026-07-09T10:00:00+00:00" hidden',
            response,
        )
        self.assertIn("window.history.replaceState", response)
        self.assertIn("event.preventDefault()", response)
        self.assertNotIn("/api/invoices/samples.pdf", response)
        self.assertNotIn("Unknown Seller", response)

        all_request = b"GET /dashboard?review=all HTTP/1.1\r\nHost: localhost\r\n\r\n"
        all_socket = TestSocket(all_request)
        Handler(all_socket, ("127.0.0.1", 12345), SimpleNamespace())
        all_response = all_socket.writer.getvalue().decode("iso-8859-1")

        self.assertIn("INV-OK-200", all_response)
        self.assertIn("Auto Approved LLC", all_response)
        self.assertIn('aria-label="Next invoice"', all_response)
        self.assertIn("pdf_id=21", all_response)
        self.assertIn(
            'data-needs-review="false" data-received="2026-07-09T10:00:00+00:00">',
            all_response,
        )
        self.assertIn("2</strong> shown", all_response)
        self.assertNotIn('name="review" value="needs_review" checked', all_response)

    def test_invoice_filters_apply_review_and_date_independently(self) -> None:
        items = [
            {"pdf_file_id": 1, "decision": "needs_review", "received_date": "2026-07-12T09:00:00Z"},
            {"pdf_file_id": 2, "decision": "approve", "received_date": "2026-07-11T09:00:00Z"},
            {"pdf_file_id": 3, "decision": "approve", "received_date": "2026-06-01T09:00:00Z"},
        ]

        review_only = _filter_invoice_items(
            items,
            review_filter="needs_review",
        )
        custom_range = _filter_invoice_items(
            items,
            review_filter="all",
            date_from=date(2026, 7, 10),
            date_to=date(2026, 7, 12),
        )

        self.assertEqual([item["pdf_file_id"] for item in review_only], [1])
        self.assertEqual([item["pdf_file_id"] for item in custom_range], [1, 2])

    def test_mail_pdf_endpoint_serves_owned_stored_pdf(self) -> None:
        class MailIntegration:
            def __init__(self, pdf_path: Path) -> None:
                self.pdf_path = pdf_path
                self.owner_user_id: str | None = None
                self.pdf_file_id: int | None = None

            def get_invoice_pdf_file(
                self, *, owner_user_id: str, pdf_file_id: int
            ) -> dict[str, object]:
                self.owner_user_id = owner_user_id
                self.pdf_file_id = pdf_file_id
                return {"path": self.pdf_path, "filename": "invoice.pdf"}

        with tempfile.TemporaryDirectory() as tmp:
            pdf_path = Path(tmp, "invoice.pdf")
            pdf_path.write_bytes(b"%PDF-1.4\nfrom-db\n%%EOF")
            mail = MailIntegration(pdf_path)

            class Handler(ZampRequestHandler):
                mail_integration = mail  # type: ignore[assignment]

                def log_message(self, format: str, *args: object) -> None:
                    pass

                def _authenticated_api_user(self) -> tuple[str, list[str]]:
                    return "user-123", ["zamp_session=refreshed; Path=/"]

            request = b"GET /api/mail/pdfs/20 HTTP/1.1\r\nHost: localhost\r\n\r\n"
            test_socket = TestSocket(request)
            Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
            response = test_socket.writer.getvalue()

        header, body = response.split(b"\r\n\r\n", 1)
        self.assertIn(b" 200 ", header.splitlines()[0])
        self.assertIn(b"Content-Type: application/pdf", header)
        self.assertIn(b'Content-Disposition: inline; filename="invoice.pdf"', header)
        self.assertIn(b"Set-Cookie: zamp_session=refreshed; Path=/", header)
        self.assertEqual(body, b"%PDF-1.4\nfrom-db\n%%EOF")
        self.assertEqual(mail.owner_user_id, "user-123")
        self.assertEqual(mail.pdf_file_id, 20)

    def test_mail_invoices_api_returns_authenticated_queue_rows(self) -> None:
        class MailIntegration:
            def __init__(self) -> None:
                self.owner_user_id: str | None = None

            def list_invoices(
                self, *, owner_user_id: str, limit: int = 100, offset: int = 0
            ) -> list[dict[str, object]]:
                self.owner_user_id = owner_user_id
                return [
                    {
                        "pdf_file_id": 42,
                        "vendor": "Acme Supplies LLC",
                        "invoice_number": "INV-1045",
                        "parse_status": "parsed",
                        "decision": "approve",
                    }
                ]

        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def _authenticated_api_user(self) -> tuple[str, list[str]] | None:
                return "user-123", []

        mail = MailIntegration()
        Handler.mail_integration = mail  # type: ignore[assignment]
        request = b"GET /api/mail/invoices?limit=10 HTTP/1.1\r\nHost: localhost\r\n\r\n"
        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue().decode("iso-8859-1")
        _, body = response.split("\r\n\r\n", 1)
        payload = json.loads(body)

        self.assertIn(" 200 ", response.splitlines()[0])
        self.assertEqual(mail.owner_user_id, "user-123")
        self.assertEqual(payload["invoices"][0]["pdf_file_id"], 42)

    def test_mail_invoices_api_rejects_invalid_pagination(self) -> None:
        class MailIntegration:
            def __init__(self) -> None:
                self.called = False

            def list_invoices(
                self, *, owner_user_id: str, limit: int = 100, offset: int = 0
            ) -> list[dict[str, object]]:
                self.called = True
                return []

        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def _authenticated_api_user(self) -> tuple[str, list[str]] | None:
                return "user-123", []

        for query, message in (
            ("limit=0", "limit must be between 1 and 500."),
            ("limit=501", "limit must be between 1 and 500."),
            ("offset=-1", "offset must be greater than or equal to 0."),
        ):
            with self.subTest(query=query):
                mail = MailIntegration()
                Handler.mail_integration = mail  # type: ignore[assignment]
                request = (
                    f"GET /api/mail/invoices?{query} HTTP/1.1\r\n"
                    "Host: localhost\r\n"
                    "\r\n"
                ).encode("ascii")
                test_socket = TestSocket(request)
                Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
                response = test_socket.writer.getvalue().decode("iso-8859-1")
                _, body = response.split("\r\n\r\n", 1)
                payload = json.loads(body)

                self.assertIn(" 400 ", response.splitlines()[0])
                self.assertEqual(payload["error"], message)
                self.assertFalse(mail.called)

    def test_mail_invoice_overlay_api_returns_pdf(self) -> None:
        class MailIntegration:
            def __init__(self) -> None:
                self.request: dict[str, object] | None = None

            def invoice_overlay_pdf(
                self, *, owner_user_id: str, pdf_file_id: int, box_mode: str = "parsed"
            ) -> bytes:
                self.request = {
                    "owner_user_id": owner_user_id,
                    "pdf_file_id": pdf_file_id,
                    "box_mode": box_mode,
                }
                return b"%PDF-1.4\n%%EOF\n"

        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

            def _authenticated_api_user(self) -> tuple[str, list[str]] | None:
                return "user-123", ["refreshed=1"]

        mail = MailIntegration()
        Handler.mail_integration = mail  # type: ignore[assignment]
        request = b"GET /api/mail/invoices/42/overlay.pdf?boxes=all HTTP/1.1\r\nHost: localhost\r\n\r\n"
        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue()
        header, body = response.split(b"\r\n\r\n", 1)

        self.assertIn(b" 200 ", header.splitlines()[0])
        self.assertIn(b"Content-Type: application/pdf", header)
        self.assertIn(b"Set-Cookie: refreshed=1", header)
        self.assertEqual(body, b"%PDF-1.4\n%%EOF\n")
        self.assertEqual(
            mail.request,
            {"owner_user_id": "user-123", "pdf_file_id": 42, "box_mode": "all"},
        )

    def test_gmail_webhook_accepts_header_secret(self) -> None:
        class MailIntegration:
            def __init__(self) -> None:
                self.payload: dict[str, object] | None = None

            def handle_gmail_pubsub(
                self, *, payload: dict[str, object], subscription: str | None = None
            ) -> dict[str, object]:
                self.payload = payload
                return {"accepted": True}

        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        mail = MailIntegration()
        Handler.config = SimpleNamespace(gmail_webhook_secret="webhook-secret")
        Handler.mail_integration = mail  # type: ignore[assignment]
        body = json.dumps(
            {"subscription": "projects/p/subscriptions/s", "message": {}}
        ).encode("utf-8")
        request = (
            b"POST /webhooks/gmail/pubsub HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"X-Zamp-Webhook-Secret: webhook-secret\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"\r\n"
            + body
        )

        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue().decode("iso-8859-1")

        self.assertIn(" 200 ", response.splitlines()[0])
        self.assertEqual(
            mail.payload, {"subscription": "projects/p/subscriptions/s", "message": {}}
        )

    def test_gmail_webhook_accepts_query_secret_for_compatibility(self) -> None:
        class MailIntegration:
            def __init__(self) -> None:
                self.payload: dict[str, object] | None = None

            def handle_gmail_pubsub(
                self, *, payload: dict[str, object], subscription: str | None = None
            ) -> dict[str, object]:
                self.payload = payload
                return {"accepted": True}

        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        mail = MailIntegration()
        Handler.config = SimpleNamespace(gmail_webhook_secret="webhook-secret")
        Handler.mail_integration = mail  # type: ignore[assignment]
        body = json.dumps(
            {"subscription": "projects/p/subscriptions/s", "message": {}}
        ).encode("utf-8")
        request = (
            b"POST /webhooks/gmail/pubsub?secret=webhook-secret HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"\r\n"
            + body
        )

        test_socket = TestSocket(request)
        Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue().decode("iso-8859-1")

        self.assertIn(" 200 ", response.splitlines()[0])
        self.assertEqual(
            mail.payload, {"subscription": "projects/p/subscriptions/s", "message": {}}
        )

    def test_gmail_webhook_accepts_verified_google_oidc_token(self) -> None:
        class MailIntegration:
            def __init__(self) -> None:
                self.payload: dict[str, object] | None = None

            def handle_gmail_pubsub(
                self, *, payload: dict[str, object], subscription: str | None = None
            ) -> dict[str, object]:
                self.payload = payload
                return {"accepted": True}

        class Handler(ZampRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                pass

        mail = MailIntegration()
        Handler.config = SimpleNamespace(
            app_url="https://app.example",
            gmail_webhook_secret=None,
            gmail_pubsub_oidc_audience="https://push.example/gmail",
            gmail_pubsub_oidc_service_account_email="push@example.iam.gserviceaccount.com",
        )
        Handler.mail_integration = mail  # type: ignore[assignment]
        body = json.dumps(
            {"subscription": "projects/p/subscriptions/s", "message": {}}
        ).encode("utf-8")
        request = (
            b"POST /webhooks/gmail/pubsub HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"Authorization: Bearer signed-google-token\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body)}\r\n".encode("ascii")
            + b"\r\n"
            + body
        )

        with patch("app.server.verify_google_oidc_token", return_value={"email_verified": True}) as verify:
            test_socket = TestSocket(request)
            Handler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())
        response = test_socket.writer.getvalue().decode("iso-8859-1")

        self.assertIn(" 200 ", response.splitlines()[0])
        verify.assert_called_once_with(
            "signed-google-token",
            audience="https://push.example/gmail",
            service_account_email="push@example.iam.gserviceaccount.com",
        )
        self.assertEqual(
            mail.payload, {"subscription": "projects/p/subscriptions/s", "message": {}}
        )

    def test_access_log_redacts_query_secret(self) -> None:
        request = (
            b"GET /logout?secret=super-secret&next=%2Fdashboard HTTP/1.1\r\n"
            b"Host: localhost\r\n"
            b"\r\n"
        )
        test_socket = TestSocket(request)

        with patch("builtins.print") as log:
            ZampRequestHandler(test_socket, ("127.0.0.1", 12345), SimpleNamespace())

        lines = "\n".join(str(call.args[0]) for call in log.call_args_list)
        self.assertIn("secret=REDACTED", lines)
        self.assertIn("next=%2Fdashboard", lines)
        self.assertNotIn("super-secret", lines)
