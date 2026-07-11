from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet

from app.mail_providers import pkce_code_challenge
from app.mail_service import MailIntegration, _require_granted_scopes
from app.mail_store import MailIntegrationError, TokenCipher


class TestRepo:
    def __init__(self) -> None:
        self.encrypted_code_verifier: str | None = None
        self.invoice_patterns: list[str] = []
        self.jobs: list[dict[str, object]] = []
        self.review_items: list[dict[str, object]] = []
        self.pdf_row: dict[str, object] | None = None

    def create_oauth_state(
        self,
        *,
        provider: str,
        owner_user_id: str,
        redirect_after: str | None,
        encrypted_code_verifier: str | None = None,
    ) -> str:
        self.encrypted_code_verifier = encrypted_code_verifier
        return "state-123"

    def consume_oauth_state(
        self,
        *,
        provider: str,
        state: str,
        owner_user_id: str,
    ) -> dict[str, object] | None:
        if owner_user_id != "user-123":
            return None
        return {
            "owner_user_id": "user-123",
            "encrypted_code_verifier": self.encrypted_code_verifier,
        }

    def get_invoice_match_patterns(self, *, owner_user_id: str) -> list[str]:
        return self.invoice_patterns

    def set_invoice_match_patterns(
        self, *, owner_user_id: str, patterns: list[str]
    ) -> list[str]:
        self.invoice_patterns = patterns
        return patterns

    def list_accounts(self, *, owner_user_id: str) -> list[dict[str, object]]:
        return [
            {"id": 1, "provider": "gmail", "status": "active"},
            {"id": 2, "provider": "outlook", "status": "active"},
            {"id": 3, "provider": "gmail", "status": "disconnected"},
        ]

    def list_invoice_review_items(
        self, *, owner_user_id: str, limit: int = 50
    ) -> list[dict[str, object]]:
        return self.review_items[:limit]

    def get_pdf_file_for_owner(
        self, *, owner_user_id: str, pdf_file_id: int
    ) -> dict[str, object] | None:
        return self.pdf_row

    def enqueue_job(self, **kwargs: object) -> bool:
        if any(job["unique_key"] == kwargs["unique_key"] for job in self.jobs):
            return False
        self.jobs.append(kwargs)
        return True


class TestGmail:
    def __init__(self) -> None:
        self.authorization_request: dict[str, str | None] = {}

    def authorization_url(
        self,
        *,
        redirect_uri: str,
        state: str,
        code_challenge: str | None = None,
    ) -> str:
        self.authorization_request = {
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
        }
        return "https://accounts.google.example/auth"


class TestOutlook:
    def authorization_url(self, *, redirect_uri: str, state: str) -> str:
        return "https://login.microsoft.example/auth"


class CapturingMailIntegration(MailIntegration):
    def __init__(self) -> None:
        super().__init__(
            SimpleNamespace(
                app_url="https://app.example",
                mail_frontend_redirect_url="https://front.example/mail",
            )
        )
        self._ready = True
        self._repo = TestRepo()  # type: ignore[assignment]
        self._cipher = TokenCipher(Fernet.generate_key().decode("ascii"))  # type: ignore[assignment]
        self._gmail = TestGmail()  # type: ignore[assignment]
        self._outlook = TestOutlook()  # type: ignore[assignment]
        self._storage = SimpleNamespace(root="/tmp")  # type: ignore[assignment]
        self.completed_gmail: dict[str, str | None] = {}

    def _complete_gmail_oauth(
        self, *, owner_user_id: str, code: str, code_verifier: str | None = None
    ) -> None:
        self.completed_gmail = {
            "owner_user_id": owner_user_id,
            "code": code,
            "code_verifier": code_verifier,
        }


class MailIntegrationPkceTests(unittest.TestCase):
    def test_start_gmail_oauth_stores_verifier_and_sends_code_challenge(self) -> None:
        integration = CapturingMailIntegration()

        result = integration.start_oauth(provider="gmail", owner_user_id="user-123")

        repo = integration.repo
        gmail = integration.gmail
        self.assertEqual(result["state"], "state-123")
        self.assertIsInstance(repo.encrypted_code_verifier, str)
        code_verifier = integration.cipher.decrypt(repo.encrypted_code_verifier)
        self.assertIsInstance(code_verifier, str)
        self.assertNotEqual(repo.encrypted_code_verifier, code_verifier)
        self.assertGreaterEqual(len(code_verifier or ""), 43)
        self.assertLessEqual(len(code_verifier or ""), 128)
        self.assertEqual(gmail.authorization_request["state"], "state-123")
        self.assertEqual(
            gmail.authorization_request["code_challenge"],
            pkce_code_challenge(str(code_verifier)),
        )

    def test_complete_gmail_oauth_passes_stored_verifier_to_exchange(self) -> None:
        integration = CapturingMailIntegration()
        integration.repo.encrypted_code_verifier = integration.cipher.encrypt(
            "verifier-123"
        )

        location = integration.complete_oauth(
            provider="gmail",
            state="state-123",
            code="code-123",
            owner_user_id="user-123",
        )

        self.assertEqual(location, "https://front.example/mail?mail_connected=gmail")
        self.assertEqual(
            integration.completed_gmail,
            {
                "owner_user_id": "user-123",
                "code": "code-123",
                "code_verifier": "verifier-123",
            },
        )

    def test_complete_gmail_oauth_rejects_state_for_other_user(self) -> None:
        integration = CapturingMailIntegration()
        integration.repo.encrypted_code_verifier = integration.cipher.encrypt(
            "verifier-123"
        )

        location = integration.complete_oauth(
            provider="gmail",
            state="state-123",
            code="code-123",
            owner_user_id="other-user",
        )

        self.assertEqual(
            location, "https://front.example/mail?mail_error=invalid_oauth_state"
        )
        self.assertEqual(integration.completed_gmail, {})


class MailIntegrationLifecycleTests(unittest.TestCase):
    def test_close_closes_database_and_resets_ready_state(self) -> None:
        class Database:
            def __init__(self) -> None:
                self.closed = False

            def close(self) -> None:
                self.closed = True

        database = Database()
        integration = MailIntegration(SimpleNamespace())
        integration._ready = True
        integration._repo = SimpleNamespace(database=database)  # type: ignore[assignment]
        integration._cipher = object()  # type: ignore[assignment]

        integration.close()

        self.assertTrue(database.closed)
        self.assertFalse(integration._ready)
        self.assertIsNone(integration._repo)
        self.assertIsNone(integration._cipher)

    def test_outlook_disconnect_deletes_graph_subscription(self) -> None:
        integration = MailIntegration(SimpleNamespace())
        integration._ready = True
        integration._repo = MagicMock()  # type: ignore[assignment]
        integration._repo.get_account.return_value = {
            "id": 7,
            "owner_user_id": "user-123",
            "provider": "outlook",
            "status": "active",
            "outlook_subscription_id": "subscription-7",
        }
        integration._repo.disconnect_account.return_value = True
        integration._token_manager = MagicMock()  # type: ignore[assignment]
        integration._token_manager.access_token_for.return_value = "access-token"
        integration._outlook = MagicMock()  # type: ignore[assignment]

        disconnected = integration.disconnect_account(owner_user_id="user-123", account_id=7)

        self.assertTrue(disconnected)
        integration.outlook.delete_subscription.assert_called_once_with(
            access_token="access-token",
            subscription_id="subscription-7",
        )

    def test_close_resets_state_when_database_close_raises(self) -> None:
        class Database:
            def close(self) -> None:
                raise RuntimeError("close failed")

        integration = MailIntegration(SimpleNamespace())
        integration._ready = True
        integration._repo = SimpleNamespace(database=Database())  # type: ignore[assignment]
        integration._cipher = object()  # type: ignore[assignment]

        with self.assertRaises(RuntimeError):
            integration.close()

        self.assertFalse(integration._ready)
        self.assertIsNone(integration._repo)
        self.assertIsNone(integration._cipher)


class MailOauthSetupTests(unittest.TestCase):
    def _integration(self) -> MailIntegration:
        integration = MailIntegration(
            SimpleNamespace(
                app_url="https://app.example",
                mail_frontend_redirect_url="https://front.example/mail",
            )
        )
        integration._ready = True
        integration._repo = MagicMock()  # type: ignore[assignment]
        integration._cipher = TokenCipher(Fernet.generate_key().decode("ascii"))  # type: ignore[assignment]
        integration._gmail = MagicMock()  # type: ignore[assignment]
        integration._outlook = MagicMock()  # type: ignore[assignment]
        return integration

    def test_gmail_watch_failure_does_not_write_active_account_and_lookup_is_owner_scoped(
        self,
    ) -> None:
        integration = self._integration()
        integration.gmail.exchange_code.return_value = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        }
        integration.gmail.profile.return_value = {"emailAddress": "AP@example.com"}
        integration.gmail.watch.side_effect = RuntimeError("watch failed")

        with self.assertRaisesRegex(RuntimeError, "watch failed"):
            integration._complete_gmail_oauth(
                owner_user_id="user-123",
                code="code-123",
                code_verifier="verifier-123",
            )

        integration.repo.get_account_by_provider_email.assert_called_once_with(
            owner_user_id="user-123",
            provider="gmail",
            email="ap@example.com",
        )
        integration.repo.upsert_account.assert_not_called()

    def test_outlook_subscription_failure_does_not_write_active_account(self) -> None:
        integration = self._integration()
        integration.outlook.exchange_code.return_value = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
        }
        integration.outlook.me.return_value = {"mail": "ap@example.com"}
        integration.outlook.create_subscription.side_effect = RuntimeError("subscription failed")

        with self.assertRaisesRegex(RuntimeError, "subscription failed"):
            integration._complete_outlook_oauth(owner_user_id="user-123", code="code-123")

        integration.repo.upsert_account.assert_not_called()


class GmailPubsubValidationTests(unittest.TestCase):
    def test_configured_subscription_must_be_present_and_match(self) -> None:
        integration = MailIntegration(
            SimpleNamespace(gmail_pubsub_subscription="projects/p/subscriptions/expected")
        )
        integration._ready = True
        integration._repo = MagicMock()  # type: ignore[assignment]

        with self.assertRaisesRegex(MailIntegrationError, "did not match"):
            integration.handle_gmail_pubsub(payload={"message": {}}, subscription=None)

        integration.repo.list_accounts_by_provider_email.assert_not_called()

class InvoicePatternSettingsTests(unittest.TestCase):
    def test_update_invoice_patterns_saves_patterns_and_enqueues_catchup_jobs(
        self,
    ) -> None:
        integration = CapturingMailIntegration()

        with patch("app.mail_service.secrets.token_hex", side_effect=["generation1", "generation2"]):
            patterns = integration.update_invoice_match_patterns(
                owner_user_id="user-123",
                patterns=[r"\binvoice\b", " ", r"^INV-\d+"],
            )

        self.assertEqual(patterns, [r"\binvoice\b", r"^INV-\d+"])
        self.assertEqual(
            integration.get_invoice_match_patterns(owner_user_id="user-123"), patterns
        )
        self.assertEqual(
            [job["job_type"] for job in integration.repo.jobs],
            ["gmail_fallback_sync", "outlook_delta_sync"],
        )
        self.assertEqual(
            [job["unique_key"] for job in integration.repo.jobs],
            [
                "gmail-fallback:1:settings:generation1",
                "outlook-delta:2:settings:generation1",
            ],
        )
        self.assertEqual(
            [job["payload"]["reprocess_key"] for job in integration.repo.jobs],  # type: ignore[index]
            ["generation1", "generation1"],
        )

        with patch("app.mail_service.secrets.token_hex", return_value="generation2"):
            integration.update_invoice_match_patterns(
                owner_user_id="user-123",
                patterns=[r"\binvoice\b", " ", r"^INV-\d+"],
            )

        self.assertEqual(len(integration.repo.jobs), 4)
        self.assertTrue(
            all("generation2" in str(job["unique_key"]) for job in integration.repo.jobs[2:])
        )

    def test_update_invoice_patterns_empty_list_enqueues_catchup_jobs(
        self,
    ) -> None:
        integration = CapturingMailIntegration()

        patterns = integration.update_invoice_match_patterns(
            owner_user_id="user-123", patterns=[]
        )

        self.assertEqual(patterns, [])
        self.assertEqual(
            [job["job_type"] for job in integration.repo.jobs],
            ["gmail_fallback_sync", "outlook_delta_sync"],
        )

    def test_update_invoice_patterns_rejects_invalid_regex(self) -> None:
        integration = CapturingMailIntegration()

        with self.assertRaises(MailIntegrationError):
            integration.update_invoice_match_patterns(
                owner_user_id="user-123", patterns=["["]
            )

    def test_suggest_invoice_pattern_from_filename(self) -> None:
        integration = CapturingMailIntegration()

        pattern = integration.suggest_invoice_match_pattern(filename="Bill_889.pdf")

        self.assertEqual(pattern, r"^Bill[\s._:#-]*\d+(?:\.pdf)?$")


class InvoiceReviewQueueTests(unittest.TestCase):
    def test_list_invoice_review_items_maps_database_rows_for_frontend(self) -> None:
        integration = CapturingMailIntegration()
        integration.repo.review_items = [
            {
                "attachment_id": 30,
                "pdf_file_id": 20,
                "filename": "vendor-invoice.pdf",
                "subject": "July invoice",
                "sender": "billing@example.com",
                "provider": "gmail",
                "account_email": "ap@example.com",
                "candidate_reason": "filename",
                "parse_status": "parsed",
                "parse_result": {
                    "fields": {
                        "invoice_number": {"value": "INV-DB-100"},
                        "seller": {"raw": "Database Vendor LLC\n1 Main St"},
                        "balance_due": {"amount": 216, "currency": "USD"},
                    }
                },
                "parse_warnings": ["missing tax"],
            }
        ]

        items = integration.list_invoice_review_items(owner_user_id="user-123")

        self.assertEqual(items[0]["invoice_number"], "INV-DB-100")
        self.assertEqual(items[0]["vendor"], "Database Vendor LLC")
        self.assertEqual(items[0]["amount"], "USD 216.00")
        self.assertEqual(items[0]["pdf_url"], "/api/mail/invoices/20/overlay.pdf?boxes=all")
        self.assertEqual(items[0]["warnings"], ["missing tax"])

    def test_get_invoice_pdf_file_resolves_relative_storage_path(self) -> None:
        integration = CapturingMailIntegration()
        integration.repo.pdf_row = {
            "pdf_file_id": 20,
            "filename": "invoice.pdf",
            "sha256": "abc",
            "byte_size": 12,
            "storage_path": "abc.pdf",
        }

        pdf_file = integration.get_invoice_pdf_file(
            owner_user_id="user-123",
            pdf_file_id=20,
        )

        self.assertEqual(pdf_file["filename"], "invoice.pdf")
        self.assertEqual(str(pdf_file["path"]), "/tmp/abc.pdf")

    def test_get_invoice_pdf_file_rejects_absolute_storage_path(self) -> None:
        integration = CapturingMailIntegration()
        integration.repo.pdf_row = {
            "pdf_file_id": 20,
            "filename": "invoice.pdf",
            "storage_path": "/tmp/abc.pdf",
        }

        with self.assertRaises(MailIntegrationError):
            integration.get_invoice_pdf_file(owner_user_id="user-123", pdf_file_id=20)


class ScopeValidationTests(unittest.TestCase):
    def test_required_scope_validation_rejects_missing_gmail_readonly(self) -> None:
        with self.assertRaises(MailIntegrationError):
            _require_granted_scopes(
                {"scope": "openid email"},
                ["https://www.googleapis.com/auth/gmail.readonly"],
                "Google",
            )

    def test_required_scope_validation_accepts_granted_gmail_readonly(self) -> None:
        _require_granted_scopes(
            {"scope": "openid email https://www.googleapis.com/auth/gmail.readonly"},
            ["https://www.googleapis.com/auth/gmail.readonly"],
            "Google",
        )
