from __future__ import annotations

import unittest
from unittest.mock import patch

from app.mail_webhook_auth import WebhookAuthenticationError, verify_google_oidc_token


class GoogleOidcVerificationTests(unittest.TestCase):
    def test_verifies_signature_claims_audience_and_service_account(self) -> None:
        claims = {
            "iss": "https://accounts.google.com",
            "aud": "https://app.example/webhooks/gmail/pubsub",
            "email": "push@example.iam.gserviceaccount.com",
            "email_verified": True,
        }
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            return_value=claims,
        ) as verify:
            result = verify_google_oidc_token(
                "signed-token",
                audience="https://app.example/webhooks/gmail/pubsub",
                service_account_email="push@example.iam.gserviceaccount.com",
            )

        self.assertEqual(result, claims)
        self.assertEqual(verify.call_args.kwargs["audience"], claims["aud"])

    def test_rejects_unexpected_service_account(self) -> None:
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            return_value={"email": "other@example.com", "email_verified": True},
        ):
            with self.assertRaisesRegex(WebhookAuthenticationError, "did not match"):
                verify_google_oidc_token(
                    "signed-token",
                    audience="https://app.example/webhooks/gmail/pubsub",
                    service_account_email="push@example.iam.gserviceaccount.com",
                )

    def test_rejects_invalid_signature_or_claims(self) -> None:
        with patch(
            "google.oauth2.id_token.verify_oauth2_token",
            side_effect=ValueError("bad signature"),
        ):
            with self.assertRaisesRegex(WebhookAuthenticationError, "Invalid Google OIDC"):
                verify_google_oidc_token(
                    "invalid-token",
                    audience="https://app.example/webhooks/gmail/pubsub",
                )
