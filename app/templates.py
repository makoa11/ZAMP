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
  <meta name="theme-color" content="#f5f3ee">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&amp;family=Manrope:wght@500;600;700&amp;display=swap" rel="stylesheet">
  <link rel="stylesheet" href="/static/styles.css">
</head>
<body>
  {body}
  <script>
    document.addEventListener("wheel", function (event) {{
      var target = event.target;
      if (
        target instanceof HTMLInputElement &&
        target.type === "number" &&
        document.activeElement === target
      ) {{
        target.blur();
      }}
    }}, true);
  </script>
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
<main class="auth-shell auth-shell-split">
  <section class="auth-layout">
    <aside class="auth-aside" aria-label="ZAMP overview">
      <div class="auth-brand">
        <div class="brand-mark" aria-hidden="true">Z</div>
        <div>
          <p class="eyebrow">ZAMP</p>
          <p class="auth-brand-line">Invoice operations</p>
        </div>
      </div>

      <div class="auth-copy">
        <p class="auth-kicker">Calm workflow</p>
        <h1 class="auth-hero-title">Review invoices with structure, context, and clear next steps.</h1>
        <p class="auth-hero-text">
          Keep the queue, decision reasoning, and source document aligned so approvers can move quickly
          without losing confidence.
        </p>
      </div>

      <div class="auth-feature-list" aria-label="Product highlights">
        <div class="auth-feature-card">
          <span class="auth-feature-index">01</span>
          <div>
            <h2>Focused triage</h2>
            <p>Needs review, accepted items, and date filtering stay in one view.</p>
          </div>
        </div>
        <div class="auth-feature-card">
          <span class="auth-feature-index">02</span>
          <div>
            <h2>Decision support</h2>
            <p>Checks already completed and missing context are separated clearly.</p>
          </div>
        </div>
        <div class="auth-feature-card">
          <span class="auth-feature-index">03</span>
          <div>
            <h2>Document traceability</h2>
            <p>The PDF, normalized data, and audit reasoning remain connected.</p>
          </div>
        </div>
      </div>
    </aside>

    <section class="login-panel auth-card" aria-labelledby="login-title">
      <div class="auth-card-header">
        <div class="brand-row">
          <div class="brand-mark brand-mark-soft" aria-hidden="true">Z</div>
          <div>
            <p class="eyebrow">Welcome back</p>
            <h1 id="login-title">Sign in</h1>
          </div>
        </div>
        <a class="auth-switch-chip" href="/signup">Create account</a>
      </div>

      <p class="auth-panel-copy">Choose your sign-in method and continue to the invoice queue.</p>

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
<main class="auth-shell auth-shell-split">
  <section class="auth-layout">
    <aside class="auth-aside" aria-label="ZAMP overview">
      <div class="auth-brand">
        <div class="brand-mark" aria-hidden="true">Z</div>
        <div>
          <p class="eyebrow">ZAMP</p>
          <p class="auth-brand-line">Invoice operations</p>
        </div>
      </div>

      <div class="auth-copy">
        <p class="auth-kicker">Structured onboarding</p>
        <h1 class="auth-hero-title">Set up an account that fits the review workflow from day one.</h1>
        <p class="auth-hero-text">
          Start with password or email OTP and keep the same calm review experience across the queue,
          evidence, and approval steps.
        </p>
      </div>

      <div class="auth-feature-list" aria-label="Product highlights">
        <div class="auth-feature-card">
          <span class="auth-feature-index">01</span>
          <div>
            <h2>Shared queue</h2>
            <p>Managers and reviewers can work through the same invoice state model.</p>
          </div>
        </div>
        <div class="auth-feature-card">
          <span class="auth-feature-index">02</span>
          <div>
            <h2>Action clarity</h2>
            <p>Each invoice highlights what is done, what is missing, and what to do next.</p>
          </div>
        </div>
        <div class="auth-feature-card">
          <span class="auth-feature-index">03</span>
          <div>
            <h2>Audit trail</h2>
            <p>Supporting details remain attached to every decision and document.</p>
          </div>
        </div>
      </div>
    </aside>

    <section class="login-panel auth-card" aria-labelledby="signup-title">
      <div class="auth-card-header">
        <div class="brand-row">
          <div class="brand-mark brand-mark-soft" aria-hidden="true">Z</div>
          <div>
            <p class="eyebrow">Account setup</p>
            <h1 id="signup-title">Create account</h1>
          </div>
        </div>
        <a class="auth-switch-chip" href="/login">Sign in</a>
      </div>

      <p class="auth-panel-copy">Choose a signup method and create access to the invoice workspace.</p>

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
    <div class="topbar-actions">
      <a class="button-link secondary-link" href="/invoice-samples">Invoice samples</a>
      <form class="logout-form" method="post" action="/logout">
        <input type="hidden" name="_csrf" value="{_e(csrf_token)}">
        <button class="secondary" type="submit">Log out</button>
      </form>
    </div>
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
        <button class="secondary" type="button" data-connect-provider="gmail">Connect Gmail</button>
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
    </div>
    <div class="mail-status" data-invoice-pattern-status role="status"></div>
    <div class="pattern-helper" data-invoice-pattern-dropzone>
      <label for="invoice-pattern-filename">Sample filename</label>
      <div class="pattern-helper-row">
        <input id="invoice-pattern-filename" data-invoice-pattern-filename type="text" autocomplete="off" placeholder="INV-2024-001.pdf">
        <div class="pattern-actions">
          <button class="secondary" type="button" data-generate-invoice-pattern>Generate regex</button>
          <button type="button" data-save-invoice-patterns>Save patterns</button>
        </div>
      </div>
      <label class="file-picker" for="invoice-pattern-file">
        <span data-invoice-pattern-file-label>Choose PDF</span>
      </label>
      <input id="invoice-pattern-file" class="sr-only-file" data-invoice-pattern-file type="file" accept="application/pdf,.pdf">
    </div>
    <label for="invoice-patterns">Patterns</label>
    <textarea id="invoice-patterns" class="pattern-input" data-invoice-patterns rows="6" spellcheck="false" placeholder="^INV-\\d+"></textarea>
  </section>

  <section class="data-panel mail-panel" aria-labelledby="ai-extraction-title">
    <div class="panel-heading">
      <div>
        <p class="label">Last-resort extraction</p>
        <h2 id="ai-extraction-title">Gemini fallback</h2>
      </div>
    </div>
    <label class="ai-consent-control" for="use-ai-extraction">
      <input id="use-ai-extraction" data-use-ai-extraction type="checkbox">
      <span>
        <strong>Use Gemini when local full-page OCR fails</strong>
        <small>The invoice PDF will be sent to Google Gemini using the credentials configured by your administrator. Static extraction and local OCR always run first.</small>
      </span>
    </label>
    <div class="mail-status" data-ai-extraction-status role="status"></div>
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
    var invoicePatternFileLabelEl = document.querySelector("[data-invoice-pattern-file-label]");
    var invoicePatternDropzoneEl = document.querySelector("[data-invoice-pattern-dropzone]");
    var generateInvoicePatternButton = document.querySelector("[data-generate-invoice-pattern]");
    var saveInvoicePatternsButton = document.querySelector("[data-save-invoice-patterns]");
    var useAiExtractionEl = document.querySelector("[data-use-ai-extraction]");
    var aiExtractionStatusEl = document.querySelector("[data-ai-extraction-status]");
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
      accounts = (accounts || []).filter(function (account) {{
        return account.status !== "disconnected";
      }});
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

    function loadExtractionSettings() {{
      if (!useAiExtractionEl) return;
      setStatus(aiExtractionStatusEl, "Loading AI preference.", "");
      fetch("/api/mail/extraction-settings")
        .then(function (response) {{
          return response.json().then(function (body) {{
            if (!response.ok) throw new Error(body.error || "Could not load extraction settings.");
            return body;
          }});
        }})
        .then(function (body) {{
          useAiExtractionEl.checked = body.use_ai === true;
          setStatus(aiExtractionStatusEl, "", "");
        }})
        .catch(function (error) {{
          setStatus(aiExtractionStatusEl, error.message, "error");
        }});
    }}

    function saveExtractionSettings() {{
      if (!useAiExtractionEl) return;
      useAiExtractionEl.disabled = true;
      setStatus(aiExtractionStatusEl, "Saving AI preference.", "");
      fetch("/api/mail/extraction-settings", {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{use_ai: useAiExtractionEl.checked}})
      }})
        .then(function (response) {{
          return response.json().then(function (body) {{
            if (!response.ok) throw new Error(body.error || "Could not save extraction settings.");
            return body;
          }});
        }})
        .then(function (body) {{
          useAiExtractionEl.checked = body.use_ai === true;
          setStatus(aiExtractionStatusEl, "AI preference saved.", "success");
        }})
        .catch(function (error) {{
          useAiExtractionEl.checked = !useAiExtractionEl.checked;
          setStatus(aiExtractionStatusEl, error.message, "error");
        }})
        .finally(function () {{
          useAiExtractionEl.disabled = false;
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

    if (useAiExtractionEl) {{
      useAiExtractionEl.addEventListener("change", saveExtractionSettings);
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
        if (invoicePatternFileLabelEl) invoicePatternFileLabelEl.textContent = file.name;
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
        if (invoicePatternFileLabelEl && filename) invoicePatternFileLabelEl.textContent = filename;
        suggestInvoicePattern(filename || "");
      }});
    }}

    loadAccounts();
    loadInvoicePatterns();
    loadExtractionSettings();
  }})();
</script>{expiry_script}
"""
    return page("Dashboard - ZAMP", body)


def invoice_samples_page(
    *,
    samples: list[dict[str, Any]],
    papers: list[dict[str, Any]],
    templates: list[dict[str, str]],
    active_paper: str,
    active_template: str | None,
    seed: int,
    count: int,
) -> str:
    paper_links = " ".join(
        f'<a class="tab{" active" if paper["slug"] == active_paper else ""}" '
        f'href="/invoice-samples?paper={_e(str(paper["slug"]))}&seed={seed}&count={count}">'
        f'{_e(str(paper["label"]))}</a>'
        for paper in papers
    )
    template_options_html = "\n".join(
        f'<option value="{_e(template["slug"])}"'
        f'{" selected" if template["slug"] == active_template else ""}>'
        f'{_e(template["name"])} - {_e(template["industry"])}</option>'
        for template in templates
    )
    invoices = "\n".join(_invoice_preview(sample) for sample in samples)
    body = f"""
<main class="invoice-gallery-shell">
  <header class="topbar invoice-gallery-topbar">
    <div>
      <p class="eyebrow">Synthetic invoice generation</p>
      <h1>Invoice variations</h1>
    </div>
    <a class="button-link secondary-link" href="/dashboard">Dashboard</a>
  </header>

  <section class="invoice-controls" aria-label="Invoice generation controls">
    <div class="tabs invoice-paper-tabs">
      {paper_links}
    </div>
    <form class="invoice-filter" method="get" action="/invoice-samples">
      <input type="hidden" name="paper" value="{_e(active_paper)}">
      <label for="invoice-template">Template
        <select id="invoice-template" name="template">
          <option value=""{" selected" if not active_template else ""}>All 15 base templates</option>
          {template_options_html}
        </select>
      </label>
      <label for="invoice-count">Count
        <input id="invoice-count" name="count" type="number" min="1" max="60" value="{count}">
      </label>
      <label for="invoice-seed">Seed
        <input id="invoice-seed" name="seed" type="number" min="1" value="{seed}">
      </label>
      <button type="submit">Generate</button>
      <a class="button-link secondary-link" href="/api/invoices/samples?paper={_e(active_paper)}&seed={seed}&count={count}{'&template=' + _e(active_template) if active_template else ''}">JSON</a>
      <a class="button-link secondary-link" href="/api/invoices/samples.pdf?paper={_e(active_paper)}&seed={seed}&count={count}{'&template=' + _e(active_template) if active_template else ''}">PDF</a>
    </form>
  </section>

  <section class="invoice-gallery" aria-label="Generated invoice samples">
    {invoices}
  </section>
</main>
"""
    return page("Invoice variations - ZAMP", body)


def invoice_showcase_page(*, documents: list[dict[str, Any]]) -> str:
    groups: list[str] = []
    for document in documents:
        group = str(document["group"])
        if group not in groups:
            groups.append(group)

    sections = []
    for group in groups:
        cards = []
        for document in documents:
            if document["group"] != group:
                continue
            tags = "".join(
                f'<span class="showcase-tag">{_e(str(tag))}</span>'
                for tag in document["tags"]
            )
            slug = _e(str(document["slug"]))
            title = _e(str(document["title"]))
            preview_pages = "".join(
                f'<img class="showcase-page" '
                f'src="/showcase/{slug}/pages/{page_number}.png" '
                f'alt="{title}, page {page_number} of {int(document["page_count"])}" '
                f'loading="lazy">'
                for page_number in range(1, int(document["page_count"]) + 1)
            )
            cards.append(
                f"""
      <article class="showcase-card">
        <header class="showcase-card-header">
          <div>
            <p class="showcase-page-count">{int(document["page_count"])} PDF pages</p>
            <h2>{title}</h2>
            <p>{_e(str(document["description"]))}</p>
          </div>
          <div class="showcase-tags" aria-label="Coverage">{tags}</div>
        </header>
        <div class="showcase-preview" aria-label="{title} PDF preview">
          {preview_pages}
        </div>
      </article>"""
            )
        sections.append(
            f"""
    <section class="showcase-section" aria-labelledby="showcase-{_e(group.lower().replace(' ', '-'))}">
      <div class="showcase-section-heading">
        <p class="eyebrow">PDF corpus</p>
        <h1 id="showcase-{_e(group.lower().replace(' ', '-'))}">{_e(group)}</h1>
      </div>
      <div class="showcase-grid">{''.join(cards)}</div>
    </section>"""
        )

    body = f"""
<main class="showcase-shell">
  <header class="showcase-hero">
    <p class="eyebrow">Invoice generation coverage</p>
    <h1>Invoice PDF showcase</h1>
    <p>One representative PDF for every clean invoice type, accounts payable scenario, rendering stress case, page size, and scan degradation. PDFs load as they enter the viewport.</p>
  </header>
  {''.join(sections)}
</main>
"""
    return page("Invoice PDF showcase - ZAMP", body)


def _invoice_preview(sample: dict[str, Any]) -> str:
    template = sample["template"]
    paper = sample["paper"]
    data = sample["data"]
    scale = "0.54"
    if paper["slug"] == "a4-half-horizontal":
        scale = "0.62"
    elif paper["slug"] == "a4-third-horizontal":
        scale = "0.68"
    style = (
        f'--paper-width:{paper["width_mm"]}mm;'
        f'--paper-height:{paper["height_mm"]}mm;'
        f'--preview-scale:{scale};'
        f'--accent:{template["accent"]};'
        f'--invoice-secondary:{template["secondary"]};'
        f'--invoice-ink:{template["ink"]};'
    )
    components = "\n".join(_invoice_component(component, sample) for component in sample["components"])
    score = sample.get("layout_score") or {}
    return f"""
<article class="invoice-sample">
  <div class="invoice-sample-heading">
    <div>
      <p class="label">{_e(template["industry"])}</p>
      <h2>{_e(template["name"])}</h2>
    </div>
    <span class="status-badge">{_e(paper["label"])}</span>
  </div>
  <div class="invoice-frame" style="{style}">
    <div class="invoice-paper paper-{_e(paper["slug"])} family-{_e(template["layout_family"])} table-{_e(template["table_style"])} logo-{_e(template["logo_shape"])} font-{_e(str(template.get("font_style", "system")))}" style="{style}">
      {components}
    </div>
  </div>
  <dl class="invoice-layout-meta">
    <div><dt>Components</dt><dd>{len(sample["components"])}</dd></div>
    <div><dt>Rows</dt><dd>{_e(str(score.get("line_item_count", len(data["items"]))))}</dd></div>
    <div><dt>Density</dt><dd>{_e(str(score.get("density", "")))}</dd></div>
  </dl>
</article>
"""


def _invoice_component(component: dict[str, Any], sample: dict[str, Any]) -> str:
    kind = component["kind"]
    variant = component.get("variant")
    variant_class = f' variant-{_e(str(variant))}' if isinstance(variant, str) and variant else ""
    table_density_class = ""
    if kind == "items-table":
        density = _table_visual_density(sample["data"])
        table_density_class = f" table-density-{_e(density)}" if density else ""
    style = (
        f'left:{component["x_mm"]}mm;'
        f'top:{component["y_mm"]}mm;'
        f'width:{component["width_mm"]}mm;'
        f'height:{component["height_mm"]}mm;'
    )
    return (
        f'<section class="invoice-component invoice-{_e(kind)}{variant_class}{table_density_class}" '
        f'data-component="{_e(kind)}" style="{style}">'
        f'{_invoice_component_body(kind, sample)}'
        "</section>"
    )


def _invoice_component_body(kind: str, sample: dict[str, Any]) -> str:
    data = sample["data"]
    labels = data.get("labels") if isinstance(data.get("labels"), dict) else {}
    seller = data["seller"]
    buyer = data["buyer"]
    template = sample["template"]
    if kind in {"accent-band", "accent-rail"}:
        return ""
    if kind == "watermark":
        return f'<span>{_e(_initials(seller["name"]))}</span>'
    if kind == "company-header":
        return f"""
<div class="invoice-company-header-mark">{_e(_initials(seller["name"]))}</div>
<div class="invoice-company-header-main">
  <strong>{_e(seller["name"])}</strong>
  <span>{_e(seller["line1"])} / {_e(seller["city"])}</span>
</div>
<div class="invoice-company-header-meta">
  <span>{_e(seller["email"])}</span>
  <span>{_e(seller.get("tax_id"))}</span>
</div>
"""
    if kind == "logo":
        return (
            f'<div class="invoice-logo-mark">{_e(_initials(seller["name"]))}</div>'
            f'<div class="invoice-logo-text">{_e(seller["name"])}</div>'
        )
    if kind == "title":
        return (
            f'<div class="invoice-title-word">{_e(labels.get("document_title", "Invoice"))}</div>'
            f'<div class="invoice-title-sub">{_e(template["name"])}</div>'
        )
    if kind == "seller":
        return _address_block(str(labels.get("seller", "From")), seller, tax_id=seller.get("tax_id"))
    if kind == "buyer":
        return _address_block(str(labels.get("buyer", "Bill to")), buyer)
    if kind == "invoice-meta":
        return f"""
<dl class="invoice-facts">
  <div><dt>{_e(labels.get("invoice_number", "No."))}</dt><dd>{_e(data["invoice_number"])}</dd></div>
  <div><dt>{_e(labels.get("purchase_order", "PO"))}</dt><dd>{_e(data["purchase_order"])}</dd></div>
  <div><dt>{_e(labels.get("status", "Status"))}</dt><dd>{_e(data["status"])}</dd></div>
</dl>
"""
    if kind == "dates":
        return f"""
<dl class="invoice-facts invoice-date-facts">
  <div><dt>{_e(labels.get("issue_date", "Issued"))}</dt><dd>{_e(data.get("issue_date_display", data["issue_date"]))}</dd></div>
  <div><dt>{_e(labels.get("due_date", "Due"))}</dt><dd>{_e(data.get("due_date_display", data["due_date"]))}</dd></div>
  <div><dt>{_e(labels.get("terms", "Terms"))}</dt><dd>{_e(data["terms"])}</dd></div>
</dl>
"""
    if kind == "items-table":
        return _items_table(data)
    if kind == "totals":
        return _totals(data)
    if kind == "payment":
        payment = data["payment"]
        return f"""
<h3>{_e(labels.get("payment", "Payment"))}</h3>
<p>{_e(payment["method"])} {_e(payment["account"])}</p>
<p>{_e(payment["reference"])}</p>
<p>{_e(payment["remit_to"])}</p>
"""
    if kind == "terms":
        return f"<p>{_e(data['notes'])}</p>"
    if kind == "footer":
        footer_note = data.get("footer_note", "")
        if not footer_note:
            return ""
        return f"<h3>Notice</h3><p>{_e(footer_note)}</p>"
    if kind == "stamp":
        return "<div class=\"invoice-stamp-text\">Approved</div>"
    if kind == "barcode":
        return "<div class=\"invoice-bars\" aria-hidden=\"true\"><span></span><span></span><span></span><span></span><span></span><span></span></div>"
    if kind == "timeline":
        return "<h3>Milestones</h3><p>Issued / Approved / Payable</p>"
    if kind == "remittance":
        return f"<h3>{_e(labels.get('payment', 'Remittance'))}</h3><p>{_e(data['payment']['remit_to'])} / {_e(labels.get('invoice_number', 'Ref'))} {_e(data['invoice_number'])}</p>"
    if kind in {
        "signature",
        "approver",
        "insurance",
        "work-order",
        "tax-summary",
        "packing",
        "quality",
        "schedule",
        "deposit",
        "itinerary",
        "sla",
    }:
        label = kind.replace("-", " ").title()
        return f"<h3>{_e(label)}</h3><p>{_e(data['purchase_order'])} / {_e(data['terms'])}</p>"
    return ""


def _address_block(label: str, entity: dict[str, str], *, tax_id: str | None = None) -> str:
    tax_line = f"<p>{_e(tax_id)}</p>" if tax_id else ""
    return f"""
<h3>{_e(label)}</h3>
<p class="invoice-entity-name">{_e(entity["name"])}</p>
<p>{_e(entity["line1"])}</p>
<p>{_e(entity["city"])}</p>
<p>{_e(entity["email"])}</p>
{tax_line}
"""


def _items_table(data: dict[str, Any]) -> str:
    table = data.get("table") if isinstance(data.get("table"), dict) else {}
    columns = table.get("columns") if isinstance(table.get("columns"), list) else []
    if not columns:
        columns = [
            {"key": "item", "label": "Item"},
            {"key": "quantity", "label": "Qty", "numeric": True},
            {"key": "unit_price", "label": "Rate", "numeric": True},
            {"key": "amount", "label": "Amount", "numeric": True},
        ]
    variant = str(table.get("variant") or "standard-desc")
    density = _table_visual_density(data)
    density_class = f" table-density-{_e(density)}" if density else ""
    rows = "\n".join(
        f"""
    <tr>
      {_table_cells(item, columns, data)}
    </tr>
"""
        for item in data["items"]
    )
    summary_row = _table_total_row(columns, data) if bool(table.get("total_in_table")) else ""
    headings = "\n".join(
        f'<th class="{_column_class(column)}">{_e(str(column.get("label", "")))}</th>'
        for column in columns
    )
    return f"""
<table class="invoice-table schema-{_e(variant)}{density_class}">
  <thead>
    <tr>
      {headings}
    </tr>
  </thead>
  <tbody>
    {rows}
    {summary_row}
  </tbody>
</table>
"""


def _table_total_row(columns: list[dict[str, Any]], data: dict[str, Any]) -> str:
    cells = "\n".join(
        f'<td class="{_column_class(column)}">'
        f'{_table_total_cell_value(columns, index, str(column.get("key", "")), data)}'
        "</td>"
        for index, column in enumerate(columns)
    )
    return f"""
    <tr class="invoice-table-total-row">
      {cells}
    </tr>
"""


def _table_total_cell_value(
    columns: list[dict[str, Any]],
    index: int,
    key: str,
    data: dict[str, Any],
) -> str:
    labels = data.get("labels") if isinstance(data.get("labels"), dict) else {}
    label_column_index = _total_label_column_index(columns)
    amount_column_index = _last_column_index(columns, {"amount", "taxable"})
    quantity_column_index = _first_column_index(columns, {"quantity", "quantity_unit"})
    if index == label_column_index:
        total_label = labels.get("balance_due") or labels.get("subtotal") or "TOTAL"
        return f'<strong>{_e(str(total_label).upper())}</strong><span>{_e(labels.get("subtotal", "Total Sum"))}</span>'
    if index == quantity_column_index:
        return _e(str(data.get("total_quantity", "")))
    if index == amount_column_index:
        return f'<strong>{_table_numeric_value(_money(float(data.get("balance_due", 0)), data), key, data)}</strong>'
    return ""


def _total_label_column_index(columns: list[dict[str, Any]]) -> int:
    preferred = {
        "item",
        "item_plain",
        "description",
        "service_date",
    }
    for index, column in enumerate(columns):
        if column.get("key") in preferred and not column.get("numeric"):
            return index
    for index, column in enumerate(columns):
        if not column.get("numeric"):
            return index
    return 0


def _first_column_index(columns: list[dict[str, Any]], keys: set[str]) -> int | None:
    for index, column in enumerate(columns):
        if column.get("key") in keys:
            return index
    return None


def _last_column_index(columns: list[dict[str, Any]], keys: set[str]) -> int | None:
    for index in range(len(columns) - 1, -1, -1):
        if columns[index].get("key") in keys:
            return index
    return None


def _table_cells(item: dict[str, Any], columns: list[dict[str, Any]], data: dict[str, Any]) -> str:
    return "\n".join(
        f'<td class="{_column_class(column)}">'
        f'{_table_cell_value(item, str(column.get("key", "")), data)}'
        "</td>"
        for column in columns
    )


def _column_class(column: dict[str, Any]) -> str:
    prefix = "number " if column.get("numeric") else ""
    return f'{prefix}invoice-col-{_e(str(column.get("key", "value")))}'


def _table_cell_value(item: dict[str, Any], key: str, data: dict[str, Any]) -> str:
    table = data.get("table") if isinstance(data.get("table"), dict) else {}
    show_description = bool(table.get("show_description", True))
    if key == "item":
        description = (
            f'<span>{_e(str(item.get("description", "")))}</span>'
            if show_description and item.get("description")
            else ""
        )
        return f'<strong>{_e(str(item.get("name", "")))}</strong>{description}'
    if key == "item_plain":
        return f'<strong>{_e(str(item.get("name", "")))}</strong>'
    if key == "line":
        return _e(str(item.get("line", "")))
    if key == "sku":
        return _e(str(item.get("sku", "")))
    if key == "hsn":
        return _e(str(item.get("hsn", "")))
    if key == "service_date":
        return _e(str(item.get("service_date_display", item.get("service_date", ""))))
    if key == "quantity":
        return _e(str(item.get("quantity", "")))
    if key == "quantity_unit":
        return _e(str(item.get("quantity_display", item.get("quantity", ""))))
    if key == "unit_price":
        return _money(float(item.get("unit_price", 0)), data)
    if key == "amount":
        return _table_numeric_value(_money(float(item.get("amount", 0)), data), key, data)
    if key == "taxable":
        return _table_numeric_value(
            _money(float(item.get("taxable_amount", item.get("amount", 0))), data),
            key,
            data,
        )
    if key == "description":
        return _e(str(item.get("description", "")))
    return _e(str(item.get(key, "")))


def _table_numeric_value(value: str, key: str, data: dict[str, Any]) -> str:
    if key in {"amount", "taxable"} and _table_visual_density(data) == "amount_boundary_collision":
        return f'<span class="invoice-print-drift">{_e(value)}</span>'
    return value


def _table_visual_density(data: dict[str, Any]) -> str:
    table = data.get("table") if isinstance(data.get("table"), dict) else {}
    density = table.get("visual_density")
    return str(density) if density else ""


def _totals(data: dict[str, Any]) -> str:
    labels = data.get("labels") if isinstance(data.get("labels"), dict) else {}
    return f"""
<dl class="invoice-totals-list">
  <div><dt>{_e(labels.get("subtotal", "Subtotal"))}</dt><dd>{_money(data["subtotal"], data)}</dd></div>
  <div><dt>{_e(labels.get("discount", "Discount"))}</dt><dd>{_money(data["discount"], data)}</dd></div>
  <div><dt>{_e(labels.get("tax", "Tax"))}</dt><dd>{_money(data["tax"], data)}</dd></div>
  <div><dt>{_e(labels.get("shipping", "Shipping"))}</dt><dd>{_money(data["shipping"], data)}</dd></div>
  <div><dt>{_e(labels.get("paid", "Paid"))}</dt><dd>{_money(data["paid"], data)}</dd></div>
  <div class="invoice-total-row"><dt>{_e(labels.get("balance_due", "Balance due"))}</dt><dd>{_money(data["balance_due"], data)}</dd></div>
</dl>
"""


def _money(value: float, data: dict[str, Any]) -> str:
    currency = str(data.get("currency", "USD"))
    formatting = data.get("formatting") if isinstance(data.get("formatting"), dict) else {}
    style = str(formatting.get("money_style", "code-prefix-2dp"))
    decimals = int(formatting.get("decimals", 2))
    amount = _formatted_amount(value, decimals=decimals, comma_decimal="comma" in style, spaced="space" in style)
    if style.startswith("plain"):
        return amount
    unit = _currency_symbol(currency) if "symbol" in style else currency
    if "suffix" in style:
        return f"{amount} {unit}" if len(unit) > 1 else f"{amount}{unit}"
    separator = "" if len(unit) == 1 else " "
    return f"{unit}{separator}{amount}"


def _currency_symbol(currency: str) -> str:
    return {
        "USD": "$",
        "INR": "₹",
        "EUR": "€",
        "GBP": "£",
        "AED": "د.إ",
        "SGD": "S$",
        "CAD": "C$",
        "AUD": "A$",
        "CNY": "¥",
        "JPY": "¥",
        "ZAR": "R",
        "MXN": "Mex$",
    }.get(currency, currency)


def _formatted_amount(value: float, *, decimals: int, comma_decimal: bool, spaced: bool) -> str:
    amount = f"{float(value):,.{decimals}f}"
    if decimals == 0:
        amount = amount.split(".", 1)[0]
    if spaced:
        amount = amount.replace(",", " ")
    if comma_decimal:
        amount = amount.replace(",", "_").replace(".", ",").replace("_", ".")
    return amount


def _initials(name: str) -> str:
    parts = [part[0] for part in name.replace("&", " ").split() if part[:1]]
    return "".join(parts[:2]).upper() or "Z"


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
