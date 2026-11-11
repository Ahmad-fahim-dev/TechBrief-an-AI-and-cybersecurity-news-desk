import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from backend.app.api.routes import router
from backend.app.config import Settings, get_settings
from backend.app.database import Base, add_article_image_columns, build_engine, build_session_factory
from backend.app.models import AppSettings, DailyReport
from backend.app.services.scheduler import build_scheduler, scheduled_generation
from backend.app.services.governance import ensure_builtin_governance

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


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

    app = FastAPI(title="TechBref Intelligence API", version="1.0.0", lifespan=lifespan)
    app.state.config = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.scheduler = scheduler if start_scheduler else None
    app.state.job = {"status": "idle", "started_at": None, "finished_at": None, "error": None}
    app.state.job_task = None

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
