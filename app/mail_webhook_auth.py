from __future__ import annotations

from typing import Any


class WebhookAuthenticationError(RuntimeError):
    """Raised when a provider webhook identity cannot be verified."""


def verify_google_oidc_token(
    token: str,
    *,
    audience: str,
    service_account_email: str | None = None,
) -> dict[str, Any]:
    try:
        from google.auth.transport.requests import Request
        from google.oauth2 import id_token
    except ImportError as exc:
        raise WebhookAuthenticationError("Google OIDC verification is unavailable.") from exc

    try:
        claims = id_token.verify_oauth2_token(token, Request(), audience=audience)
    except Exception as exc:
        raise WebhookAuthenticationError("Invalid Google OIDC token.") from exc

    if not isinstance(claims, dict):
        raise WebhookAuthenticationError("Google OIDC claims were invalid.")
    email = claims.get("email")
    email_verified = claims.get("email_verified")
    if not isinstance(email, str) or email_verified not in {True, "true"}:
        raise WebhookAuthenticationError("Google OIDC email claim was not verified.")
    if service_account_email and email.casefold() != service_account_email.casefold():
        raise WebhookAuthenticationError("Google OIDC service account did not match configuration.")
    return claims
