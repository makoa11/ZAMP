from __future__ import annotations

import html
import json
from typing import Any


def _e(value: str | None) -> str:
    return html.escape(value or "", quote=True)


def page(title: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)}</title>
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  {body}
</body>
</html>"""


def login_page(
    *,
    csrf_token: str,
    mode: str = "password",
    message: str | None = None,
    message_kind: str = "error",
    otp_email: str | None = None,
    email_verification_email: str | None = None,
) -> str:
    email_verification_active = bool(email_verification_email)
    password_active = mode != "otp"
    otp_active = mode == "otp"
    notice = ""
    if message:
        notice = f'<div class="notice notice-{_e(message_kind)}" role="status">{_e(message)}</div>'

    body = f"""
<main class="auth-shell">
  <section class="login-panel" aria-labelledby="login-title">
    <div class="brand-row">
      <div class="brand-mark" aria-hidden="true">Z</div>
      <div>
        <p class="eyebrow">ZAMP</p>
        <h1 id="login-title">Sign in</h1>
      </div>
    </div>

    <div class="tabs" role="tablist" aria-label="Sign in method">
      <a class="tab{' active' if password_active else ''}" href="/login?mode=password" role="tab" aria-selected="{str(password_active).lower()}">Password</a>
      <a class="tab{' active' if otp_active else ''}" href="/login?mode=otp" role="tab" aria-selected="{str(otp_active).lower()}">Email OTP</a>
    </div>

    {notice}

    <form class="form{' hidden' if not (password_active and not email_verification_active) else ''}" method="post" action="/login">
      <input type="hidden" name="_csrf" value="{_e(csrf_token)}">
      <input type="hidden" name="action" value="password">
      <label for="password-email">Email</label>
      <input id="password-email" name="email" type="email" autocomplete="email" required>

      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password" required>

      <button type="submit">Sign in</button>
    </form>

    <form class="form{' hidden' if not (otp_active and not email_verification_active) else ''}" method="post" action="/login">
      <input type="hidden" name="_csrf" value="{_e(csrf_token)}">
      <label for="otp-email">Email</label>
      <input id="otp-email" name="email" type="email" autocomplete="email" value="{_e(otp_email)}" required>
      <label for="otp-code">Code</label>
      <input id="otp-code" name="code" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" maxlength="10" required>
      <div class="button-row">
        <button type="submit" name="action" value="otp_send" formnovalidate>Send code</button>
        <button type="submit" name="action" value="otp_verify">Verify code</button>
      </div>
    </form>

    <form class="form{' hidden' if not email_verification_active else ''}" method="post" action="/login">
      <input type="hidden" name="_csrf" value="{_e(csrf_token)}">
      <input type="hidden" name="action" value="email_verification_verify">
      <label for="login-email-verification-email">Email</label>
      <input id="login-email-verification-email" name="email" type="email" autocomplete="email" value="{_e(email_verification_email)}" required>
      <label for="login-email-verification-code">Verification code</label>
      <input id="login-email-verification-code" name="code" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" maxlength="10" required>
      <button type="submit">Verify account</button>
    </form>

    <p class="switch-line">Need an account? <a href="/signup">Create one</a></p>
  </section>
</main>
"""
    return page("Sign in - ZAMP", body)


def signup_page(
    *,
    csrf_token: str,
    mode: str = "password",
    message: str | None = None,
    message_kind: str = "error",
    otp_email: str | None = None,
    email_verification_email: str | None = None,
) -> str:
    email_verification_active = bool(email_verification_email)
    password_active = mode != "otp"
    otp_active = mode == "otp"
    notice = ""
    if message:
        notice = f'<div class="notice notice-{_e(message_kind)}" role="status">{_e(message)}</div>'

    body = f"""
<main class="auth-shell">
  <section class="login-panel" aria-labelledby="signup-title">
    <div class="brand-row">
      <div class="brand-mark" aria-hidden="true">Z</div>
      <div>
        <p class="eyebrow">ZAMP</p>
        <h1 id="signup-title">Create account</h1>
      </div>
    </div>

    <div class="tabs" role="tablist" aria-label="Signup method">
      <a class="tab{' active' if password_active else ''}" href="/signup?mode=password" role="tab" aria-selected="{str(password_active).lower()}">Password</a>
      <a class="tab{' active' if otp_active else ''}" href="/signup?mode=otp" role="tab" aria-selected="{str(otp_active).lower()}">Email OTP</a>
    </div>

    {notice}

    <form class="form{' hidden' if not (password_active and not email_verification_active) else ''}" method="post" action="/signup">
      <input type="hidden" name="_csrf" value="{_e(csrf_token)}">
      <input type="hidden" name="action" value="password">
      <div class="name-grid">
        <div>
          <label for="signup-first-name">First name</label>
          <input id="signup-first-name" name="first_name" type="text" autocomplete="given-name">
        </div>
        <div>
          <label for="signup-last-name">Last name</label>
          <input id="signup-last-name" name="last_name" type="text" autocomplete="family-name">
        </div>
      </div>

      <label for="signup-email">Email</label>
      <input id="signup-email" name="email" type="email" autocomplete="email" required>

      <label for="signup-password">Password</label>
      <input id="signup-password" name="password" type="password" autocomplete="new-password" minlength="10" required>

      <label for="signup-password-confirm">Confirm password</label>
      <input id="signup-password-confirm" name="password_confirm" type="password" autocomplete="new-password" minlength="10" required>

      <button type="submit">Create account</button>
    </form>

    <form class="form{' hidden' if not (otp_active and not email_verification_active) else ''}" method="post" action="/signup">
      <input type="hidden" name="_csrf" value="{_e(csrf_token)}">
      <label for="signup-otp-email">Email</label>
      <input id="signup-otp-email" name="email" type="email" autocomplete="email" value="{_e(otp_email)}" required>
      <label for="signup-otp-code">Code</label>
      <input id="signup-otp-code" name="code" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" maxlength="10" required>
      <div class="button-row">
        <button type="submit" name="action" value="otp_send" formnovalidate>Send code</button>
        <button type="submit" name="action" value="otp_verify">Verify code</button>
      </div>
    </form>

    <form class="form{' hidden' if not email_verification_active else ''}" method="post" action="/signup">
      <input type="hidden" name="_csrf" value="{_e(csrf_token)}">
      <input type="hidden" name="action" value="email_verification_verify">
      <label for="signup-email-verification-email">Email</label>
      <input id="signup-email-verification-email" name="email" type="email" autocomplete="email" value="{_e(email_verification_email)}" required>
      <label for="signup-email-verification-code">Verification code</label>
      <input id="signup-email-verification-code" name="code" inputmode="numeric" pattern="[0-9]*" autocomplete="one-time-code" maxlength="10" required>
      <button type="submit">Verify account</button>
    </form>

    <p class="switch-line">Already have an account? <a href="/login">Sign in</a></p>
  </section>
</main>
"""
    return page("Create account - ZAMP", body)


def dashboard_page(*, csrf_token: str, session: dict[str, Any]) -> str:
    user = session.get("user") or {}
    name = " ".join(
        part for part in [user.get("first_name"), user.get("last_name")] if isinstance(part, str)
    ).strip()
    display_name = name or user.get("email") or "Signed-in user"
    session_json = json.dumps(session, indent=2, sort_keys=True)
    expires_at = session.get("expires_at")
    expiry_script = ""
    if isinstance(expires_at, int):
        expiry_script = f"""
  <script>
    window.setTimeout(function () {{
      window.location.href = "/login";
    }}, Math.max(0, ({expires_at} * 1000) - Date.now() + 250));
  </script>"""
    body = f"""
<main class="dashboard-shell">
  <header class="topbar">
    <div>
      <p class="eyebrow">ZAMP</p>
      <h1>Dashboard</h1>
    </div>
    <form method="post" action="/logout">
      <input type="hidden" name="_csrf" value="{_e(csrf_token)}">
      <button class="secondary" type="submit">Log out</button>
    </form>
  </header>

  <section class="summary">
    <div>
      <p class="label">Signed in as</p>
      <h2>{_e(display_name)}</h2>
      <p>{_e(user.get("email"))}</p>
    </div>
    <div>
      <p class="label">Session</p>
      <p>{_e(session.get("session_id"))}</p>
    </div>
  </section>

  <section class="data-panel">
    <h2>Session data</h2>
    <pre>{_e(session_json)}</pre>
  </section>
</main>{expiry_script}
"""
    return page("Dashboard - ZAMP", body)


def error_page(status: int, message: str) -> str:
    return page(
        f"{status} - ZAMP",
        f"""
<main class="auth-shell">
  <section class="login-panel compact">
    <h1>{status}</h1>
    <p>{_e(message)}</p>
    <a class="button-link" href="/login">Back to sign in</a>
  </section>
</main>
""",
    )
