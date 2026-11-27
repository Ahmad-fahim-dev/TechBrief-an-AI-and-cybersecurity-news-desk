# TechBref

**AI & Cybersecurity Daily News Intelligence** — a local-first editorial publication that collects recent reporting, ranks it, and creates source-linked daily briefs.

## What is included

- FastAPI REST API with SQLAlchemy persistence (SQLite by default; PostgreSQL URL supported).
- Deterministic least-privilege agent governance with expiring/revocable grants, approval requests, application append-only audit history, and internal-only mock SOC drafts. Sensitive SOC execution has no configured executor and fails closed.
- RSS image metadata and publisher Open Graph images are checked and attributed when available; category illustrations are used when no safe image is found. Remote image usage remains subject to publisher terms.
- LangGraph research workflow with coordinator, AI and cybersecurity researchers, verification, ranking, summarization, and editorial nodes.
- RSS ingestion and optional Tavily news search; optional OpenAI-compatible LangChain summaries.
- A daily APScheduler job, defaulting to **9:00 AM Asia/Karachi**, plus a manual report action.
- Vanilla HTML/CSS/JavaScript mustard-framed editorial site with AI and cybersecurity coverage, trending filters, responsive topic pages, article reading view, archive, bookmarks, and settings.
- Tests use local databases and mocked research; they do not call live providers.

## Requirements

- Python 3.11 or newer.
- Optional: an OpenAI API key for generated 100–180-word summaries and a Tavily API key for search results.
- Optional for production: PostgreSQL and a managed worker/scheduler deployment.

## Run locally

From the project root in PowerShell:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt
Copy-Item .env.example .env
uvicorn backend.app.main:app --reload
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Interactive API documentation is served at `/docs` only when `EXPOSE_API_DOCS=true` (it is disabled by default so the public surface stays minimal); the health check is `/health`.

To preview the editorial layouts with clearly labelled fictional sample stories when the API is unavailable, add `?demo=1` to the homepage URL. The default live mode never fills production screens with demo headlines.

The app reads `.env` from the process working directory. Keep the real file private; `.env` and local database files are git-ignored.

## Configure providers

Set values in the local `.env`, then restart the backend:

- `OPENAI_API_KEY`: enables LangChain/OpenAI summaries. Without it, the report uses publisher descriptions and explicitly says generated analysis is unavailable; it does not pretend those descriptions were AI-written.
- `OPENAI_MODEL`: model name (default `gpt-4o-mini`).
- `TAVILY_API_KEY`: enables Tavily news search in addition to the built-in RSS feeds.
- `RSS_FEEDS_JSON`: optional JSON array of feeds. Each item must have `category` (`ai` or `cybersecurity`), `name`, and an HTTPS `url`. Invalid entries are skipped; malformed JSON fails configuration clearly.
- `DATABASE_URL`: defaults to `sqlite:///./techbref.db`. For PostgreSQL use a SQLAlchemy URL such as `postgresql+psycopg://user:password@host:5432/techbref`.
- `TIMEZONE`, `SCHEDULED_TIME`, `SCHEDULE_ENABLED`, `ARTICLES_PER_CATEGORY`, and `REQUEST_TIMEOUT_SECONDS` configure defaults. Settings can also be updated through the dashboard.
- `ADMIN_API_TOKEN`: required to unlock the AGENT GOVERNANCE dashboard, its administration APIs, and the operator actions (manual report generation, report retries, missing-image backfill, and saving Preferences). Use a randomly generated secret with at least 32 characters. The token is held in browser memory only and must be sent over HTTPS outside local development.
- `ADMIN_USER_ID`: administrator identity recorded in the governance audit log (default `admin`).
- `EXPOSE_API_DOCS`: set to `true` to serve the interactive `/docs` explorer; it is disabled by default to minimize the public API surface.

Never put provider keys in frontend code, browser storage, source control, or issue reports. Use `.env.example` only as a placeholder template.

## Sources and verification limits

Built-in feeds include OpenAI, Google DeepMind, Microsoft Research, Hugging Face, arXiv AI, CISA, NIST NVD, BleepingComputer, The Hacker News, and Krebs on Security. Feed sources can change their formats or become unavailable; failures are logged and surfaced as report warnings.

The collector enforces a rolling 24-hour publication window and requires HTTPS links. Verification status reports the evidence actually available (provider/feed metadata and link presence); a feed listing is **not** independent confirmation. The app does not infer a Critical/High/Medium/Low severity from a CVE identifier. Only source-confirmed severity is labeled, and users should consult linked advisories before taking action.

## API overview

- `GET /api/dashboard`
- `GET /api/news` (`q`, `category`, `source`, `from_date`, `to_date`, `min_importance`, `page`, `page_size`)
- `GET /api/news/{id}`
- `GET /api/reports`, `GET /api/reports/latest`, `GET /api/reports/{id}`
- `POST /api/reports/generate`, `POST /api/reports/{id}/retry` — administrator token required
- `GET /api/reports/status`
- `GET /api/settings`, `GET /api/sources`
- `PUT /api/settings` — administrator token required
- `POST /api/images/discover-missing` — administrator token required
- `POST /api/bookmarks`, `GET /api/bookmarks`, `DELETE /api/bookmarks/{id}`
- Governance endpoints (`/api/governance`, `/api/agents`, `/api/permissions`, `/api/actions`, `/api/approvals`, `/api/audit-logs`) require `Authorization: Bearer <ADMIN_API_TOKEN>`. Only configured administrator access can grant/revoke permissions or approve actions.

Manual report generation returns `202 Accepted`; poll the status endpoint. The scheduler uses a unique dated run key and APScheduler's single-instance/coalescing controls to prevent duplicate scheduled runs. Reports and failures are persisted.

The initial built-in agents receive expiring read-only and internal TechBrief-write grants. External/SOC grants require a reference and expire within 30 days. Restricted SOC action requests require explicit approval, then still fail closed at execution because no external executor is configured. Draft operations use a mock adapter that writes a proposed draft to TechBrief only; it never contacts Jira, TheHive, ServiceNow, Sentinel, Splunk, or Elastic. Audit records are append-only through the application API, not tamper-proof against database administrators.

## Security posture

Hardening applied to the service:

- **Browser security headers on every response**: `Content-Security-Policy` (local scripts/styles, the Google Fonts origins, and HTTPS article images only; inline script and framing blocked), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, `Cache-Control: no-store` for `/api` responses, and `Strict-Transport-Security` when the request arrives over HTTPS.
- **Administrator-gated operator actions**: manual report generation, report retries, missing-image backfill, and saving Preferences all require `Authorization: Bearer <ADMIN_API_TOKEN>` in addition to the governance APIs. Anonymous visitors can only read public news/report endpoints and manage the shared bookmark list.
- **Per-IP rate limiting** (in-memory, single process): expensive provider-credit operations are capped at 6 per hour per client, the admin-token surface at 30 per minute, and the public read API at 240 per minute, with `429` + `Retry-After` responses. The body-size middleware rejects oversized/invalid `Content-Length` bodies with `413`/`400`.
- **Minimal public surface**: `/docs`, `/redoc`, and `/openapi.json` are disabled unless `EXPOSE_API_DOCS=true`; the dashboard API no longer reveals whether provider keys are configured.
- **Audit log retention**: audit records older than 90 days are pruned on startup and after each scheduled run so the append-only history cannot grow without bound.
- **Log hygiene**: untrusted feed/article values are stripped of control characters before being written to log lines so they cannot forge log entries.
- **Search hardening**: `LIKE` wildcard characters in search terms are escaped so queries match literal text only.

Known residual risks to manage at deployment time: the in-memory rate limiter is per-process (use a shared limiter when scaling, and run uvicorn with `--proxy-headers` behind a trusted reverse proxy so client IPs are real); outbound article-page fetches validate DNS at request time, so a determined attacker with control of a feed could attempt a DNS-rebinding race — restrict backend egress at the network layer if this matters; bookmarks remain a shared, unauthenticated convenience feature for the single-reader deployment model.

## Tests

```powershell
python -m pytest backend\tests -q
```

Tests do not require provider credentials or live network access.

## Deployment notes

Use PostgreSQL, HTTPS termination, secret injection from the deployment platform, structured log collection, and a single scheduler owner. For multiple API replicas, move APScheduler to a dedicated worker or managed scheduler before scaling; the local scheduler is intentionally a straightforward single-process implementation. Run database migrations as a deployment step when moving beyond local development (the initial version creates tables on startup). Review API authentication and per-user authorization before exposing the service publicly. Images are embedded from their source URLs and are not downloaded or rehosted; attribution and available licensing metadata are retained, but image reuse rights must be confirmed with the publisher.
