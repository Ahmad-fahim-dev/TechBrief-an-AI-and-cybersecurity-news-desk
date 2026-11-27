import logging
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.api.routes import router
from backend.app.config import Settings, get_settings
from backend.app.database import Base, add_article_image_columns, build_engine, build_session_factory
from backend.app.models import AppSettings, DailyReport
from backend.app.services.scheduler import build_scheduler, scheduled_generation
from backend.app.services.governance import ensure_builtin_governance, prune_audit_logs

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
# Upper bound on tracked rate-limit buckets before dead entries are swept.
RATE_LIMIT_BUCKET_CAP = 4096


class RequestBodyLimitMiddleware:
    """Enforce MAX_REQUEST_BODY_BYTES on the bytes actually received.

    A Content-Length check alone misses requests that use chunked transfer
    encoding (no Content-Length header) and clients that declare a small
    Content-Length but stream more; both would otherwise be fully buffered by
    the framework. This pure-ASGI middleware counts real body bytes as they
    arrive and rejects the request the moment the limit is exceeded.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
            await self.app(scope, receive, send)
            return
        headers = {name.lower(): value for name, value in scope.get("headers") or []}
        declared = headers.get(b"content-length")
        if declared:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse({"detail": "Invalid Content-Length header"}, status_code=400)
                await response(scope, receive, send)
                return
        received = bytearray()
        while True:
            message: Message = await receive()
            if message["type"] == "http.disconnect":
                return
            received.extend(message.get("body", b""))
            if len(received) > self.max_bytes:
                await self._reject(scope, receive, send)
                return
            if not message.get("more_body"):
                break
        full_body = bytes(received)
        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": full_body, "more_body": False}

        await self.app(scope, replay_receive, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse({"detail": "Request body too large"}, status_code=413)
        await response(scope, receive, send)


# Browser security headers applied to every response. The CSP allows only local
# scripts/styles, the Google Fonts origins used by the editorial theme, and
# HTTPS article images; it blocks inline script execution and framing.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' https:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}

# In-memory per-IP sliding-window limits. Buckets: expensive operations that
# spend provider credits, the admin-token surface (slows guessing), and the
# public read API. Single-process only; deploy behind a real limiter at scale.
RATE_LIMIT_RULES = {
    "expensive": (6, 3600.0),
    "admin": (30, 60.0),
    "public": (240, 60.0),
}
ADMIN_PATH_PREFIXES = (
    "/api/governance",
    "/api/agents",
    "/api/permissions",
    "/api/actions",
    "/api/approvals",
    "/api/audit-logs",
)


def _rate_limit_bucket(path: str) -> tuple[str, int, float] | None:
    """Map a request path to (bucket, limit, window-seconds) or None."""
    if path == "/api/reports/generate" or (path.startswith("/api/reports/") and path.endswith("/retry")):
        return "expensive", *RATE_LIMIT_RULES["expensive"]
    if path == "/api/images/discover-missing":
        return "expensive", *RATE_LIMIT_RULES["expensive"]
    if path.startswith(ADMIN_PATH_PREFIXES):
        return "admin", *RATE_LIMIT_RULES["admin"]
    if path.startswith("/api/"):
        return "public", *RATE_LIMIT_RULES["public"]
    return None


def _sweep_dead_rate_limit_buckets(buckets: dict, now: float) -> None:
    """Evict rate-limit buckets that can no longer affect current limits.

    A bucket whose newest event is older than its window is equivalent to an
    empty one: the client's next request would have pruned those events and
    started a fresh window. Sweeping only once the table is large bounds the
    memory that rotating client addresses (for example an IPv6 /64) can
    consume without ever resetting an attacker's active window.
    """
    for key in [
        key
        for key, events in buckets.items()
        if not events or now - events[-1] >= RATE_LIMIT_RULES[key[0]][1]
    ]:
        buckets.pop(key, None)


def _apply_security_headers(response, request: Request) -> None:
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    scheme = request.url.scheme
    if scheme != "https":
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        scheme = forwarded or scheme
    if scheme == "https":
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


def create_app(config: Settings | None = None, start_scheduler: bool = True) -> FastAPI:
    settings = config or get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)
    scheduler = build_scheduler(session_factory, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        Base.metadata.create_all(engine)
        add_article_image_columns(engine)
        ensure_builtin_governance(session_factory)
        session = session_factory()
        try:
            prune_audit_logs(session)
            interrupted = session.scalars(select(DailyReport).where(DailyReport.status == "running")).all()
            for report in interrupted:
                report.status = "failed"
                report.warnings = '["Interrupted by application restart; retry this report."]'
            if session.get(AppSettings, 1) is None:
                session.add(AppSettings(
                    id=1,
                    timezone=settings.timezone,
                    scheduled_time=settings.scheduled_time,
                    schedule_enabled=settings.schedule_enabled,
                    articles_per_category=settings.articles_per_category,
                ))
            session.commit()
        finally:
            session.close()
        if start_scheduler:
            scheduler.start()
            app.state.refresh_schedule()
        yield
        if scheduler.running:
            scheduler.shutdown(wait=False)
        engine.dispose()

    app = FastAPI(
        title="TechBref Intelligence API",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if settings.expose_api_docs else None,
        redoc_url="/redoc" if settings.expose_api_docs else None,
        openapi_url="/openapi.json" if settings.expose_api_docs else None,
    )
    # Count-based body-size enforcement (covers chunked and lying Content-Length
    # requests). Added before the HTTP middlewares below so the outermost
    # middleware still decorates its 413/400 early returns with hardening headers.
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=MAX_REQUEST_BODY_BYTES)

    @app.middleware("http")
    async def security_headers_and_rate_limit(request: Request, call_next):
        """Outermost middleware: per-IP rate limits plus browser security headers.

        Registered last so it wraps the body-size middleware and decorates every
        response, including 413/429 early returns, with the hardening headers.
        """
        rule = _rate_limit_bucket(request.url.path)
        if rule:
            bucket, limit, window = rule
            client_ip = request.client.host if request.client else "unknown"
            key = (bucket, client_ip)
            buckets: dict[tuple, deque] = request.app.state.rate_limit_buckets
            now = time.monotonic()
            if len(buckets) >= RATE_LIMIT_BUCKET_CAP:
                _sweep_dead_rate_limit_buckets(buckets, now)
            events = buckets.setdefault(key, deque())
            while events and now - events[0] >= window:
                events.popleft()
            if len(events) >= limit:
                retry_after = max(1, int(window - (now - events[0])))
                response = JSONResponse(
                    {"detail": "Too many requests; retry later"},
                    status_code=429,
                    headers={"Retry-After": str(retry_after)},
                )
                _apply_security_headers(response, request)
                return response
            events.append(now)
        response = await call_next(request)
        _apply_security_headers(response, request)
        return response

    app.state.config = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.scheduler = scheduler if start_scheduler else None
    app.state.job = {"status": "idle", "started_at": None, "finished_at": None, "error": None}
    app.state.job_task = None
    app.state.rate_limit_buckets = {}

    def refresh_schedule() -> None:
        session = session_factory()
        try:
            options = session.get(AppSettings, 1)
            if not options:
                return
            if not options.schedule_enabled:
                if scheduler.get_job("daily-report"):
                    scheduler.remove_job("daily-report")
                return
            from apscheduler.triggers.cron import CronTrigger

            scheduler.add_job(
                scheduled_generation,
                args=[session_factory, settings],
                trigger=CronTrigger(
                    hour=int(options.scheduled_time[:2]),
                    minute=int(options.scheduled_time[3:]),
                    timezone=options.timezone,
                ),
                id="daily-report",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
                misfire_grace_time=3600,
            )
        finally:
            session.close()

    app.state.refresh_schedule = refresh_schedule
    app.include_router(router)
    static_dir = PROJECT_ROOT / "frontend"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/health", tags=["health"])
    def health():
        return {"status": "ok", "name": settings.app_name}

    return app


app = create_app()
