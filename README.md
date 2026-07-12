# ZAMP WorkOS Auth Backend

This is a small Python backend using the installed `workos` SDK. It supports:

- Email/password sign-in through WorkOS User Management.
- Email OTP sign-in through WorkOS Magic Auth.
- Email/password signup through WorkOS User Management user creation.
- Email OTP signup through WorkOS Magic Auth.
- WorkOS sealed session cookies for session validation and refresh.
- Configurable absolute session lifetime with WorkOS session revocation.
- Login, signup, dashboard, logout, and `/api/session` routes.
- Gmail and Outlook OAuth connection for invoice PDF ingestion.
- Event-driven mail webhooks with polling fallback jobs.
- Local PDF file storage with PostgreSQL metadata and job dedupe.

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
.venv/bin/pip install -r requirements.txt
.venv/bin/python main.py
```

The invoice parser uses a local adaptive extraction pipeline. It profiles each page as native text, hybrid, or scanned; keeps competing static candidates; validates dates, currencies, totals, and line-item sums; and runs local OCR only when required evidence is missing, contradictory, or degraded. Production OCR renders selected pages once, applies OpenCV orientation/deskew/contrast/threshold preprocessing, parses the full page, and reserves high-resolution region OCR for unresolved fields. Results that remain incomplete, ambiguous, inconsistent, or over budget are marked `needs_review`. Install the system `tesseract` binary as well as the Python requirements to enable OCR.

If a user explicitly enables **Use AI when local full-page OCR fails** in Settings, a
`needs_review` result that exhausted local OCR can use a last-resort AI extractor. No AI SDK is
installed. Zamp sends a provider-neutral JSON request to an administrator-configured endpoint,
strictly validates its response, reruns invoice consistency checks, and only promotes a valid
result. Transport, schema, or model failures remain `needs_review`.

Adaptive OCR settings are optional:

```env
MAIL_PARSE_OCR_RENDER_DPI=300
MAIL_PARSE_OCR_REFINEMENT_DPI=450
MAIL_PARSE_OCR_TIMEOUT_SECONDS=15
MAIL_PARSE_DOCUMENT_TIMEOUT_SECONDS=90
```

Optional AI fallback settings:

```env
AI_EXTRACTION_ENDPOINT=https://your-ai-gateway.example/v1/invoice-extract
AI_EXTRACTION_API_KEY=
AI_EXTRACTION_MODEL=your-model-name
AI_EXTRACTION_TIMEOUT_SECONDS=60
AI_EXTRACTION_MAX_PDF_BYTES=20971520
```

The endpoint contract is independent of any model vendor. Zamp sends `contract_version`, the
configured `model`, the extraction `prompt`, `response_schema`, and a `document` containing the
filename, MIME type, base64 PDF data, and locally recovered text. The endpoint must return exactly
`{"output": <schema-compliant object>}`; `output` may also be a string containing that JSON object.
The prompt and Draft 2020-12 schema are exported as `AI_INVOICE_EXTRACTION_PROMPT` and
`AI_INVOICE_EXTRACTION_SCHEMA` in `app.invoice_ai`. Switch providers by changing the endpoint and
model, or by putting a provider-specific adapter behind the same contract.

Open `http://127.0.0.1:8000/login`.

Signup is available at `http://127.0.0.1:8000/signup`.

Synthetic invoice layout samples are available at `http://127.0.0.1:8000/invoice-samples`.
The generator exposes full A4, A4/2 horizontal, and A4/3 horizontal paper formats, with 15 base templates and seeded JSON output at `/api/invoices/samples`.
PDF output is available at `/api/invoices/samples.pdf` with the same `paper`, `template`, `count`, and `seed` query parameters.
To generate the local parser test corpus, run:

```bash
.venv/bin/python -m app.generate_test_pdfs
```

Generate deterministic image-only scan degradations and benchmark exact fields, challenge tags, routes, and latency with:

```bash
.venv/bin/python scripts/generate_degraded_test_pdfs.py --input-dir storage/test_pdfs --output-dir storage/test_pdfs_degraded --limit 25
.venv/bin/python scripts/benchmark_invoice_extraction.py --input-dir storage/test_pdfs_degraded --output storage/degraded-benchmark.json
```

Use `--disable-ocr` on the benchmark command to isolate native static extraction.

By default this writes 250 PDFs and 250 expected-output manifests to `storage/test_pdfs`: 150 diverse one-invoice PDFs plus 100 stress PDFs. The standard PDFs cover every A4/A4-half/A4-third paper and base-template pairing at least three times, while repeat rounds shift capture profiles, fonts, currencies, date formats, table schemas, invoice number styles, labels, totals placement, amounts, and invoice dates.
The stress PDFs cycle through multi-page invoices, line-item tables continued across pages, notes/footers close to table bounds, side-panel totals, table-row totals, ambiguous entity labels such as `Account`, `To`, `Source`, and `Entity`, and glyph-sensitive currency rendering. Stress filenames are sequenced by fixture family, for example `invoice-stress-0001-multipage-continuation.pdf`.

Use the smaller override only when you need a quick local run:

```bash
.venv/bin/python -m app.generate_test_pdfs --pdf-count 50
```

The remaining generator options are for reproducibility and output location:

```bash
.venv/bin/python -m app.generate_test_pdfs --output-dir storage/test_pdfs --seed 1000 --date YYYY-MM-DD
```

Each manifest records normalized invoice fields, rendered display values, line items, total placement, page/table continuation metadata, key component bounding boxes, and challenge tags so parser output can be compared automatically. Standard manifests also include AP edge-case metadata when generated, including `edge_cases`, `ap_context`, and AP challenge tags such as `partial_po_consumption` and `split_po_billing`.

Each generated model includes positioned invoice components such as `company-header`, `invoice-meta`, `items-table`, `totals`, and optional payment or footer blocks. The `company-header` component carries a header variant such as centered, no-line, boxed, banded, receipt, rail, or minimal.
Generated samples also vary capture-sensitive content: compact date strings such as `DDMMYYYY`, `DDMMYY`, and `MMDDYYYY`; invoice number styles; currencies including USD, INR, EUR, GBP, AED, SGD, and others; decimal/no-decimal amount rendering; table schemas; and alternate labels such as left balance, remaining payment, billed amount, pay by, settle by, and amount open.

To visualize parser evidence boxes on top of a PDF, generate a highlighted overlay PDF:

```bash
.venv/bin/python -m app.invoice_overlay storage/test_pdfs/invoice-sample-0001-a4-ledger-clean.pdf /tmp/invoice-sample-0001-parsed.pdf
```

The output keeps the original PDF pages and appends transparent yellow rectangle overlays. By default it highlights accepted parsed field evidence only. Use `--boxes words` to highlight every extracted text word box, or `--boxes all` to include both parser evidence and word geometry.

To run the synthetic static-parser decision pipeline in one command, generate PDFs and manifests, parse them, plot parser boxes, normalize the invoice, run deterministic decisioning, and write JSON audit artifacts:

```bash
.venv/bin/python scripts/run_test_invoice_pipeline.py --generate --input-dir storage/test_pdfs --output-dir storage/test_invoice_pipeline --pdf-count 50 --seed 1000 --date YYYY-MM-DD --boxes all
```

This path does not write to PostgreSQL. It writes parsed JSON, overlay PDFs, normalized invoice JSON, decision JSON, and audit JSON per PDF, plus a summary comparing actual decisions with manifest expected decisions. To rerun the same pipeline against PDFs already present in `storage/test_pdfs`, omit `--generate`:

```bash
.venv/bin/python scripts/run_test_invoice_pipeline.py --input-dir storage/test_pdfs --output-dir storage/test_invoice_pipeline
```
Some full A4 layouts place the payable total as the last row of the item table, with total quantity populated, the rate cell intentionally blank, and the amount cell using the final payable value. Currency can render as a code or a symbol such as `$`, `Rs`, `€`, `£`, `¥`, `A$`, or `S$`.
Template metadata also includes `font_style`, and rendered samples rotate through system, serif, slab, mono, condensed, rounded, formal, industrial, humanist, geometric, courier, book, narrow, typewriter, and neo-grotesque font stacks.

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

## Mail Ingestion

Mail integration is lazy-loaded by mail endpoints and the worker. Existing auth routes still start without mail config, but mail features require PostgreSQL and provider credentials.

Required v1 mail settings:

```env
DATABASE_URL=postgresql://zamp:password@127.0.0.1:5432/zamp
MAIL_DB_POOL_MIN_SIZE=1
MAIL_DB_POOL_MAX_SIZE=10
MAIL_TOKEN_ENCRYPTION_KEY=
MAIL_PDF_STORAGE_DIR=./storage/mail_pdfs
MAIL_FRONTEND_REDIRECT_URL=http://127.0.0.1:8000/dashboard

GOOGLE_OAUTH_CLIENT_ID=
GOOGLE_OAUTH_CLIENT_SECRET=
GMAIL_PUBSUB_TOPIC=projects/your-project/topics/gmail-inbound
GMAIL_PUBSUB_SUBSCRIPTION=projects/your-project/subscriptions/gmail-inbound-push
GMAIL_PUBSUB_OIDC_AUDIENCE=https://your-app.example/webhooks/gmail/pubsub
GMAIL_PUBSUB_OIDC_SERVICE_ACCOUNT_EMAIL=gmail-push@your-project.iam.gserviceaccount.com
GMAIL_WEBHOOK_SECRET=

MICROSOFT_CLIENT_ID=
MICROSOFT_CLIENT_SECRET=
MICROSOFT_TENANT_ID=common
```

Generate the mail token key with:

```bash
.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Gmail OAuth uses the authorization code flow with S256 PKCE. The backend stores the per-request verifier encrypted with OAuth state and exchanges the callback code server-side.

Frontend-facing APIs:

- `POST /api/mail/oauth/gmail/start`
- `POST /api/mail/oauth/outlook/start`
- `GET /api/mail/oauth/{provider}/callback`
- `GET /api/mail/accounts`
- `DELETE /api/mail/accounts/{id}`
- `GET /api/mail/invoices`
- `GET /api/mail/invoices/{pdf_file_id}`
- `GET /api/mail/invoices/{pdf_file_id}/overlay.pdf`
- `GET /api/mail/pdfs/{pdf_file_id}`

Provider webhooks:

- Gmail Pub/Sub push target: `POST /webhooks/gmail/pubsub` with a Google-signed OIDC bearer token. Configure its audience and service-account email with `GMAIL_PUBSUB_OIDC_AUDIENCE` and `GMAIL_PUBSUB_OIDC_SERVICE_ACCOUNT_EMAIL`. `X-Zamp-Webhook-Secret` is available as a header fallback. A `?secret=...` query parameter is also accepted for backward compatibility, but it is less safe because it can leak into request logs.
- Outlook Graph notification target: `POST /webhooks/outlook`; notifications are validated with Graph `clientState`.

Run the ingestion worker:

```bash
.venv/bin/python -m app.mail_worker
```

The worker claims provider jobs, refreshes OAuth tokens when needed, renews Gmail watches and Outlook subscriptions hourly, and enqueues polling fallback jobs every 15 minutes by default. PDFs are saved when no dashboard regex patterns are configured; once patterns are added, a pattern must match the PDF filename, subject, or body/snippet. The dashboard can generate a regex from a sample filename or dropped local PDF name. PDFs are stored under `MAIL_PDF_STORAGE_DIR` as SHA-256-named files; Postgres stores account, message, attachment, file, webhook event, active/retry/failed job state, lightweight job dedupe keys, invoice matching metadata, parse results, normalized extractions, decisions, and simulated AP context records. Saved PDFs enqueue `parse_pdf` jobs that run the static text-layer invoice parser, targeted OCR for low-confidence parsed boxes, full-document OCR fallback when required normalized fields are missing, normalize the result, match DB-backed AP context, decide, and persist the review payload. Results route to `needs_review` when OCR still cannot complete required fields. `MAIL_PARSE_OCR_MAX_REGIONS` caps the number of targeted regions attempted per PDF.

To seed local simulated AP context from generated manifests:

```bash
.venv/bin/python scripts/seed_ap_context_records.py --owner-user-id demo-user --manifest-dir storage/test_pdfs
```
