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


def dashboard_page(
    *,
    csrf_token: str,
    session: dict[str, Any],
    mail_notice: str | None = None,
    mail_notice_kind: str = "success",
) -> str:
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
    notice = ""
    if mail_notice:
        notice = f'<div class="notice notice-{_e(mail_notice_kind)}" role="status">{_e(mail_notice)}</div>'
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

  <section class="data-panel mail-panel" aria-labelledby="mail-title">
    <div class="panel-heading">
      <div>
        <p class="label">Mail ingestion</p>
        <h2 id="mail-title">Connected mailboxes</h2>
      </div>
      <div class="mail-actions">
        <button type="button" data-connect-provider="gmail">Connect Gmail</button>
        <button class="secondary" type="button" data-connect-provider="outlook">Connect Outlook</button>
      </div>
    </div>
    {notice}
    <div class="mail-status" data-mail-status role="status"></div>
    <div class="account-list" data-mail-accounts></div>
  </section>

  <section class="data-panel mail-panel" aria-labelledby="invoice-patterns-title">
    <div class="panel-heading">
      <div>
        <p class="label">Invoice matching</p>
        <h2 id="invoice-patterns-title">Regex patterns</h2>
      </div>
      <button type="button" data-save-invoice-patterns>Save patterns</button>
    </div>
    <div class="mail-status" data-invoice-pattern-status role="status"></div>
    <div class="pattern-helper" data-invoice-pattern-dropzone>
      <label for="invoice-pattern-filename">Sample filename</label>
      <div class="pattern-helper-row">
        <input id="invoice-pattern-filename" data-invoice-pattern-filename type="text" autocomplete="off" placeholder="INV-2024-001.pdf">
        <button class="secondary" type="button" data-generate-invoice-pattern>Generate regex</button>
      </div>
      <input data-invoice-pattern-file type="file" accept="application/pdf,.pdf">
    </div>
    <label for="invoice-patterns">Patterns</label>
    <textarea id="invoice-patterns" class="pattern-input" data-invoice-patterns rows="6" spellcheck="false" placeholder="^INV-\\d+"></textarea>
  </section>

  <section class="data-panel">
    <h2>Session data</h2>
    <pre>{_e(session_json)}</pre>
  </section>
</main>
<script>
  (function () {{
    var statusEl = document.querySelector("[data-mail-status]");
    var accountsEl = document.querySelector("[data-mail-accounts]");
    var invoiceStatusEl = document.querySelector("[data-invoice-pattern-status]");
    var invoicePatternsEl = document.querySelector("[data-invoice-patterns]");
    var invoicePatternFilenameEl = document.querySelector("[data-invoice-pattern-filename]");
    var invoicePatternFileEl = document.querySelector("[data-invoice-pattern-file]");
    var invoicePatternDropzoneEl = document.querySelector("[data-invoice-pattern-dropzone]");
    var generateInvoicePatternButton = document.querySelector("[data-generate-invoice-pattern]");
    var saveInvoicePatternsButton = document.querySelector("[data-save-invoice-patterns]");
    var buttons = Array.prototype.slice.call(document.querySelectorAll("[data-connect-provider]"));

    function setStatus(target, message, kind) {{
      if (!target) return;
      target.textContent = message || "";
      target.className = "mail-status" + (message ? " visible " + (kind || "") : "");
    }}

    function formatDate(value) {{
      if (!value) return "Not set";
      var date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString();
    }}

    function providerLabel(provider) {{
      return provider === "gmail" ? "Gmail" : "Outlook";
    }}

    function renderAccounts(accounts) {{
      if (!accountsEl) return;
      accountsEl.innerHTML = "";
      if (!accounts.length) {{
        var empty = document.createElement("p");
        empty.className = "empty-state";
        empty.textContent = "No mailboxes connected.";
        accountsEl.appendChild(empty);
        return;
      }}
      accounts.forEach(function (account) {{
        var row = document.createElement("div");
        row.className = "account-row";

        var mark = document.createElement("div");
        mark.className = "provider-mark";
        mark.textContent = providerLabel(account.provider).charAt(0);

        var details = document.createElement("div");
        details.className = "account-details";

        var title = document.createElement("div");
        title.className = "account-title";
        title.textContent = account.email || "Unknown mailbox";

        var meta = document.createElement("div");
        meta.className = "account-meta";
        var renewal = account.provider === "gmail"
          ? account.gmail_watch_expiration
          : account.outlook_subscription_expiration;
        meta.textContent = providerLabel(account.provider) + " - " + formatDate(renewal);

        details.appendChild(title);
        details.appendChild(meta);
        if (account.last_error) {{
          var error = document.createElement("div");
          error.className = "account-error";
          error.textContent = account.last_error;
          details.appendChild(error);
        }}

        var badge = document.createElement("span");
        badge.className = "status-badge " + (account.status || "unknown");
        badge.textContent = account.status || "unknown";

        var disconnect = document.createElement("button");
        disconnect.className = "secondary compact-button";
        disconnect.type = "button";
        disconnect.textContent = "Disconnect";
        disconnect.addEventListener("click", function () {{
          disconnectAccount(account.id, disconnect);
        }});

        row.appendChild(mark);
        row.appendChild(details);
        row.appendChild(badge);
        row.appendChild(disconnect);
        accountsEl.appendChild(row);
      }});
    }}

    function loadAccounts() {{
      setStatus(statusEl, "Loading mailboxes.", "");
      fetch("/api/mail/accounts")
        .then(function (response) {{
          return response.json().then(function (body) {{
            if (!response.ok) throw new Error(body.error || "Could not load mailboxes.");
            return body;
          }});
        }})
        .then(function (body) {{
          renderAccounts(body.accounts || []);
          setStatus(statusEl, "", "");
        }})
        .catch(function (error) {{
          setStatus(statusEl, error.message, "error");
        }});
    }}

    function loadInvoicePatterns() {{
      setStatus(invoiceStatusEl, "Loading patterns.", "");
      fetch("/api/mail/invoice-patterns")
        .then(function (response) {{
          return response.json().then(function (body) {{
            if (!response.ok) throw new Error(body.error || "Could not load patterns.");
            return body;
          }});
        }})
        .then(function (body) {{
          if (invoicePatternsEl) invoicePatternsEl.value = (body.patterns || []).join("\\n");
          setStatus(invoiceStatusEl, body.patterns && body.patterns.length ? "" : "No patterns saved.", "");
        }})
        .catch(function (error) {{
          setStatus(invoiceStatusEl, error.message, "error");
        }});
    }}

    function saveInvoicePatterns() {{
      if (!invoicePatternsEl || !saveInvoicePatternsButton) return;
      saveInvoicePatternsButton.disabled = true;
      setStatus(invoiceStatusEl, "Saving patterns.", "");
      var patterns = invoicePatternsEl.value.split(/\\r?\\n/).map(function (line) {{
        return line.trim();
      }}).filter(Boolean);
      fetch("/api/mail/invoice-patterns", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{patterns: patterns}})
      }})
        .then(function (response) {{
          return response.json().then(function (body) {{
            if (!response.ok) throw new Error(body.error || "Could not save patterns.");
            return body;
          }});
        }})
        .then(function (body) {{
          invoicePatternsEl.value = (body.patterns || []).join("\\n");
          setStatus(
            invoiceStatusEl,
            body.patterns && body.patterns.length ? "Patterns saved." : "No patterns saved.",
            "success"
          );
        }})
        .catch(function (error) {{
          setStatus(invoiceStatusEl, error.message, "error");
        }})
        .finally(function () {{
          saveInvoicePatternsButton.disabled = false;
        }});
    }}

    function appendInvoicePattern(pattern) {{
      if (!invoicePatternsEl || !pattern) return;
      var patterns = invoicePatternsEl.value.split(/\\r?\\n/).map(function (line) {{
        return line.trim();
      }}).filter(Boolean);
      if (patterns.indexOf(pattern) === -1) {{
        patterns.push(pattern);
      }}
      invoicePatternsEl.value = patterns.join("\\n");
    }}

    function suggestInvoicePattern(filename) {{
      if (!generateInvoicePatternButton) return;
      filename = (filename || "").trim();
      if (!filename) {{
        setStatus(invoiceStatusEl, "Filename is required.", "error");
        return;
      }}
      generateInvoicePatternButton.disabled = true;
      setStatus(invoiceStatusEl, "Generating regex.", "");
      fetch("/api/mail/invoice-patterns/suggest", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{filename: filename}})
      }})
        .then(function (response) {{
          return response.json().then(function (body) {{
            if (!response.ok) throw new Error(body.error || "Could not generate regex.");
            return body;
          }});
        }})
        .then(function (body) {{
          appendInvoicePattern(body.pattern);
          setStatus(invoiceStatusEl, "Regex added. Save patterns to apply.", "success");
        }})
        .catch(function (error) {{
          setStatus(invoiceStatusEl, error.message, "error");
        }})
        .finally(function () {{
          generateInvoicePatternButton.disabled = false;
        }});
    }}

    function connectProvider(provider, button) {{
      button.disabled = true;
      setStatus(statusEl, "Starting " + providerLabel(provider) + " connection.", "");
      fetch("/api/mail/oauth/" + provider + "/start", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{}})
      }})
        .then(function (response) {{
          return response.json().then(function (body) {{
            if (!response.ok) throw new Error(body.error || "Could not start connection.");
            return body;
          }});
        }})
        .then(function (body) {{
          window.location.href = body.authorization_url;
        }})
        .catch(function (error) {{
          button.disabled = false;
          setStatus(statusEl, error.message, "error");
        }});
    }}

    function disconnectAccount(accountId, button) {{
      button.disabled = true;
      setStatus(statusEl, "Disconnecting mailbox.", "");
      fetch("/api/mail/accounts/" + accountId, {{method: "DELETE"}})
        .then(function (response) {{
          return response.json().then(function (body) {{
            if (!response.ok) throw new Error(body.error || "Could not disconnect mailbox.");
            return body;
          }});
        }})
        .then(function () {{
          loadAccounts();
        }})
        .catch(function (error) {{
          button.disabled = false;
          setStatus(statusEl, error.message, "error");
        }});
    }}

    buttons.forEach(function (button) {{
      button.addEventListener("click", function () {{
        connectProvider(button.getAttribute("data-connect-provider"), button);
      }});
    }});

    if (saveInvoicePatternsButton) {{
      saveInvoicePatternsButton.addEventListener("click", saveInvoicePatterns);
    }}

    if (generateInvoicePatternButton) {{
      generateInvoicePatternButton.addEventListener("click", function () {{
        suggestInvoicePattern(invoicePatternFilenameEl ? invoicePatternFilenameEl.value : "");
      }});
    }}

    if (invoicePatternFileEl) {{
      invoicePatternFileEl.addEventListener("change", function () {{
        var file = invoicePatternFileEl.files && invoicePatternFileEl.files[0];
        if (!file) return;
        if (invoicePatternFilenameEl) invoicePatternFilenameEl.value = file.name;
        suggestInvoicePattern(file.name);
      }});
    }}

    if (invoicePatternDropzoneEl) {{
      invoicePatternDropzoneEl.addEventListener("dragover", function (event) {{
        event.preventDefault();
        invoicePatternDropzoneEl.classList.add("dragging");
      }});
      invoicePatternDropzoneEl.addEventListener("dragleave", function () {{
        invoicePatternDropzoneEl.classList.remove("dragging");
      }});
      invoicePatternDropzoneEl.addEventListener("drop", function (event) {{
        event.preventDefault();
        invoicePatternDropzoneEl.classList.remove("dragging");
        var file = event.dataTransfer.files && event.dataTransfer.files[0];
        var filename = file ? file.name : event.dataTransfer.getData("text");
        if (invoicePatternFilenameEl) invoicePatternFilenameEl.value = filename || "";
        suggestInvoicePattern(filename || "");
      }});
    }}

    loadAccounts();
    loadInvoicePatterns();
  }})();
</script>{expiry_script}
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
