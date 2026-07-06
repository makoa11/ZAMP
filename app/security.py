from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def generate_csrf_token(secret: str) -> str:
    return sign_value(secrets.token_urlsafe(32), secret)


def sign_value(value: str, secret: str) -> str:
    payload = {"v": value, "iat": int(time.time())}
    encoded_payload = _b64encode(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256)
    return f"{encoded_payload}.{_b64encode(signature.digest())}"


def unsign_value(token: str | None, secret: str, max_age_seconds: int | None = None) -> str | None:
    if not token or "." not in token:
        return None

    encoded_payload, encoded_signature = token.rsplit(".", 1)
    expected = _b64encode(
        hmac.new(secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(encoded_signature, expected):
        return None

    try:
        payload: dict[str, Any] = json.loads(_b64decode(encoded_payload).decode("utf-8"))
    except Exception:
        return None

    issued_at = int(payload.get("iat", 0))
    if max_age_seconds is not None and time.time() - issued_at > max_age_seconds:
        return None

    value = payload.get("v")
    return value if isinstance(value, str) else None


def valid_signed_pair(form_value: str | None, cookie_value: str | None, secret: str) -> bool:
    if not form_value or not cookie_value:
        return False
    if not hmac.compare_digest(form_value, cookie_value):
        return False
    return unsign_value(cookie_value, secret, max_age_seconds=3600) is not None

