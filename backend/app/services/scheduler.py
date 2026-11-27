import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.models import AppSettings, DailyReport
from backend.app.services.report_service import generate_report
from backend.app.services.governance import prune_audit_logs

logger = logging.getLogger(__name__)

async def scheduled_generation(session_factory: sessionmaker, config: Settings) -> None:
    session = session_factory()
    try:
        options = session.get(AppSettings, 1)
        if not options or not options.schedule_enabled:
            return
        prune_audit_logs(session)
        # Skip when any other generation (manual run or another worker) is
        # mid-flight: concurrent runs duplicate provider spend and collide on
        # article upserts.
        in_flight = session.scalar(
            select(func.count()).select_from(DailyReport).where(DailyReport.status == "running")
        ) or 0
        if in_flight:
            logger.info("Skipping scheduled report: another generation is already in progress")
            return
        now = datetime.now(ZoneInfo(options.timezone))
        key = f"scheduled:{now.date().isoformat()}"
        await generate_report(session_factory, config.model_copy(update={
            "timezone": options.timezone,
            "articles_per_category": options.articles_per_category,
        }), run_key=key)
    except Exception:
        logger.exception("Scheduled report generation failed")
    finally:
        session.close()


def build_scheduler(session_factory: sessionmaker, config: Settings) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=config.timezone)
    scheduler.add_job(
        scheduled_generation,
        args=[session_factory, config],
        trigger=CronTrigger(hour=int(config.scheduled_time[:2]), minute=int(config.scheduled_time[3:]), timezone=config.timezone),
        id="daily-report",
        replace_existing=True,
        coalesce=True,
        max_instances=1,
        misfire_grace_time=3600,
    )
    return scheduler
