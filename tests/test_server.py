from __future__ import annotations

import io
import json
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.server import ZampHTTPServer, ZampRequestHandler


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
                self.detail_request: tuple[str, int] | None = None

            def list_invoices(
                self, *, owner_user_id: str, limit: int = 100, offset: int = 0
            ) -> list[dict[str, object]]:
                self.list_owner_user_id = owner_user_id
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
                        "filename": "approved.pdf",
                        "invoice_number": "INV-APPROVED",
                        "vendor": "Auto Approved LLC",
                        "decision": "approve",
                    }
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
        self.assertEqual(mail.detail_request, ("user-123", 20))
        self.assertIn("INV-DB-100", response)
        self.assertIn("Database Vendor LLC", response)
        self.assertIn("Invoice amount is outside tolerance.", response)
        self.assertIn("Amount match", response)
        self.assertIn("DATABASE VENDOR LLC", response)
        self.assertIn('/api/mail/invoices/20/overlay.pdf?boxes=all', response)
        self.assertNotIn('/api/mail/pdfs/20', response)
        self.assertNotIn("Auto Approved LLC", response)
        self.assertNotIn("/api/invoices/samples.pdf", response)
        self.assertNotIn("Unknown Seller", response)

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

    def test_gmail_webhook_accepts_query_secret(self) -> None:
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
