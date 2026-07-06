from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from cryptography.fernet import Fernet


class ConfigError(RuntimeError):
    """Raised when required application configuration is missing or invalid."""


def _load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        values[key.strip()] = value
    return values


def _env(name: str, env_file_values: Dict[str, str], default: str | None = None) -> str | None:
    return os.environ.get(name) or env_file_values.get(name) or default


def _bool_env(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, env_file_values: Dict[str, str], default: int) -> int:
    value = _env(name, env_file_values, str(default))
    try:
        parsed = int(str(value))
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise ConfigError(f"{name} must be greater than 0.")
    return parsed


def _derive_signing_secret(secret: str, purpose: str) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"zamp:{purpose}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class AppConfig:
    workos_api_key: str
    workos_client_id: str
    workos_cookie_password: str
    csrf_secret: str
    otp_email_cookie_secret: str
    session_metadata_cookie_secret: str
    email_verification_cookie_secret: str
    host: str
    port: int
    app_url: str
    session_cookie_name: str
    session_metadata_cookie_name: str
    session_max_age_seconds: int
    otp_email_cookie_name: str
    email_verification_cookie_name: str
    csrf_cookie_name: str
    cookie_secure: bool

    @property
    def logout_return_url(self) -> str:
        return f"{self.app_url.rstrip('/')}/login"


def load_config(root: Path | None = None) -> AppConfig:
    project_root = root or Path.cwd()
    env_file_values = _load_env_file(project_root / ".env")

    api_key = _env("WORKOS_API_KEY", env_file_values)
    client_id = _env("WORKOS_CLIENT_ID", env_file_values)
    cookie_password = _env("WORKOS_COOKIE_PASSWORD", env_file_values)
    app_signing_secret = _env("APP_SIGNING_SECRET", env_file_values)

    missing = [
        name
        for name, value in {
            "WORKOS_API_KEY": api_key,
            "WORKOS_CLIENT_ID": client_id,
            "WORKOS_COOKIE_PASSWORD": cookie_password,
            "APP_SIGNING_SECRET": app_signing_secret,
        }.items()
        if not value
    ]
    if missing:
        raise ConfigError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + ". See .env.example."
        )

    if len(str(app_signing_secret)) < 32:
        raise ConfigError(
            "APP_SIGNING_SECRET must be at least 32 characters. Generate one with: "
            ".venv/bin/python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )

    try:
        Fernet(str(cookie_password).encode("utf-8"))
    except Exception as exc:
        raise ConfigError(
            "WORKOS_COOKIE_PASSWORD must be a Fernet key. Generate one with: "
            ".venv/bin/python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from exc

    host = _env("HOST", env_file_values, "127.0.0.1") or "127.0.0.1"
    port_raw = _env("PORT", env_file_values, "8000") or "8000"
    app_url = _env("APP_URL", env_file_values, f"http://{host}:{port_raw}") or f"http://{host}:{port_raw}"
    secure_default = app_url.startswith("https://")

    return AppConfig(
        workos_api_key=str(api_key),
        workos_client_id=str(client_id),
        workos_cookie_password=str(cookie_password),
        csrf_secret=_derive_signing_secret(str(app_signing_secret), "csrf"),
        otp_email_cookie_secret=_derive_signing_secret(str(app_signing_secret), "otp-email-cookie"),
        session_metadata_cookie_secret=_derive_signing_secret(
            str(app_signing_secret),
            "session-metadata-cookie",
        ),
        email_verification_cookie_secret=_derive_signing_secret(
            str(app_signing_secret),
            "email-verification-cookie",
        ),
        host=host,
        port=int(port_raw),
        app_url=app_url.rstrip("/"),
        session_cookie_name=_env("SESSION_COOKIE_NAME", env_file_values, "zamp_session") or "zamp_session",
        session_metadata_cookie_name=_env(
            "SESSION_METADATA_COOKIE_NAME",
            env_file_values,
            "zamp_session_meta",
        )
        or "zamp_session_meta",
        session_max_age_seconds=_int_env("SESSION_MAX_AGE_SECONDS", env_file_values, 60 * 60 * 24 * 7),
        otp_email_cookie_name=_env("OTP_EMAIL_COOKIE_NAME", env_file_values, "zamp_otp_email") or "zamp_otp_email",
        email_verification_cookie_name=_env(
            "EMAIL_VERIFICATION_COOKIE_NAME",
            env_file_values,
            "zamp_email_verification",
        )
        or "zamp_email_verification",
        csrf_cookie_name=_env("CSRF_COOKIE_NAME", env_file_values, "zamp_csrf") or "zamp_csrf",
        cookie_secure=_bool_env(_env("COOKIE_SECURE", env_file_values), secure_default),
    )
