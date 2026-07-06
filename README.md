# ZAMP WorkOS Auth Backend

This is a small Python backend using the installed `workos` SDK. It supports:

- Email/password sign-in through WorkOS User Management.
- Email OTP sign-in through WorkOS Magic Auth.
- Email/password signup through WorkOS User Management user creation.
- Email OTP signup through WorkOS Magic Auth.
- WorkOS sealed session cookies for session validation and refresh.
- Configurable absolute session lifetime with WorkOS session revocation.
- Login, signup, dashboard, logout, and `/api/session` routes.

## Setup

Create `.env` from `.env.example` and fill in your WorkOS values.

Generate the WorkOS session cookie password with:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Generate a separate app signing secret for CSRF tokens and signed local cookies with:

```bash
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Then run:

```bash
.venv/bin/python main.py
```

Open `http://127.0.0.1:8000/login`.

Signup is available at `http://127.0.0.1:8000/signup`.

## WorkOS Configuration

Enable User Management authentication methods in the WorkOS dashboard:

- `POST /login` handles email + password, Magic Auth code send, Magic Auth code verification, and email-verification code completion.
- `POST /signup` handles email + password signup, Magic Auth code send, Magic Auth code verification, and email-verification code completion.

The Magic Auth OTP sequence follows WorkOS directly: send a code for an email address, then authenticate with the same email address and code.

For production, set:

```env
APP_URL=https://your-domain.example
COOKIE_SECURE=true
SESSION_MAX_AGE_SECONDS=604800
```

`SESSION_MAX_AGE_SECONDS` controls when the app revokes the WorkOS session and clears local cookies. Set it to the number of seconds you want sessions to remain valid, for example `3600` for one hour.

On successful authentication, the backend schedules a WorkOS session revocation for that session ID and marks that session revoked locally when the timer fires. It also stores a separate signed metadata cookie with the WorkOS session ID and creation time, so every authenticated request enforces the same absolute lifetime across refreshes. The dashboard redirects back to login when the configured lifetime is reached. The scheduled revocation is in-process; if the server restarts before the timer fires, the next request with that session still revokes and clears it when it is over age.

The backend stores the WorkOS access and refresh tokens only inside the encrypted sealed session cookie.
`WORKOS_COOKIE_PASSWORD` is used only for WorkOS sealed sessions. `APP_SIGNING_SECRET` is expanded into separate HMAC keys for CSRF, OTP email, session metadata, and email-verification cookies.
