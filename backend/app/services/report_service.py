import json
import logging
import uuid
from datetime import datetime, timezone
from collections.abc import Callable
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.config import Settings
from backend.app.models import AppSettings, Article, DailyReport, utc_now
from backend.app.services.collector import configured_feeds
from backend.app.services.governance import authorize_agent_action, ensure_builtin_governance
from backend.app.workflows.news_graph import build_research_graph

logger = logging.getLogger(__name__)


async def generate_report(
    session_factory: sessionmaker,
    config: Settings,
    run_key: str | None = None,
    on_progress: Callable[[str], None] | None = None,
) -> DailyReport:
    ensure_builtin_governance(session_factory)
    session: Session = session_factory()
    run_key = run_key or f"manual:{uuid.uuid4()}"
    local_now = datetime.now(ZoneInfo(config.timezone))
    report = DailyReport(
        run_key=run_key,
        report_date=local_now.date().isoformat(),
        status="running",
    )
    try:
        session.add(report)
        session.commit()
        session.refresh(report)
    except IntegrityError:
        session.rollback()
        existing = session.scalar(select(DailyReport).where(DailyReport.run_key == run_key))
        if existing and existing.status != "failed":
            session.close()
            return existing
        if existing:
            existing.status = "running"
            existing.created_at = utc_now()
            existing.warnings = "[]"
            session.commit()
            session.refresh(existing)
            report = existing
        else:
            session.close()
            raise

    try:
        stored_settings = session.get(AppSettings, 1)
        enabled = set(json.loads(stored_settings.enabled_categories)) if stored_settings else {"ai", "cybersecurity"}
        count = stored_settings.articles_per_category if stored_settings else config.articles_per_category
        update = {"articles_per_category": count}
        preferred = json.loads(stored_settings.preferred_sources) if stored_settings else []
        if preferred:
            selected = {name.casefold() for name in preferred}
            update["rss_feeds_json"] = json.dumps([
                feed for feed in configured_feeds(config) if feed["name"].casefold() in selected
            ])
        session.commit()
        workflow_config = config.model_copy(update=update)
        workflow_input = {
            "categories": enabled,
            "raw_articles": [],
            "warnings": [],
        }
        state = {"categories": enabled, "raw_articles": [], "articles": [], "warnings": []}
        async for update in build_research_graph(workflow_config, session_factory).astream(
            workflow_input, stream_mode="updates"
        ):
            for node_name, values in update.items():
                if on_progress:
                    on_progress(node_name)
                for key, value in values.items():
                    if key in {"raw_articles", "warnings"}:
                        state[key].extend(value)
                    else:
                        state[key] = value
        if not authorize_agent_action(
            session_factory,
            agent_id="report_persistence",
            action="article.write",
            resource="techbrief:articles",
            correlation_id=run_key,
        ):
            raise PermissionError("Report persistence agent is not authorized to write articles")
        if not authorize_agent_action(
            session_factory,
            agent_id="report_persistence",
            action="report.write",
            resource="techbrief:reports",
            correlation_id=run_key,
        ):
            raise PermissionError("Report persistence agent is not authorized to write reports")

        article_ids_by_url: dict[str, int] = {}
        for data in state.get("articles", []):
            article = session.scalar(select(Article).where(Article.source_url == data["source_url"]))
            if article is None:
                article = Article(**data)
                session.add(article)
                session.flush()
            else:
                article.title = data["title"]
                article.description = data["description"]
                article.summary = data.get("summary", "")
                article.importance_score = data.get("importance_score", 0)
                article.score_reasons = data.get("score_reasons", "")
                article.verification_status = data.get("verification_status", "unverified")
                article.impact_analysis = data.get("impact_analysis", "")
                article.severity = data.get("severity")
                article.cve_ids = data.get("cve_ids", "")
                for field in (
                    "image_url",
                    "image_alt",
                    "image_credit",
                    "image_source_name",
                    "image_source_url",
                    "image_license",
                    "image_type",
                    "is_illustrative",
                    "image_verified",
                    "image_checked_at",
                ):
                    setattr(article, field, data.get(field))
            article_ids_by_url[data["source_url"]] = article.id

        report_data = state["report"]
        report.ai_news = json.dumps([article_ids_by_url[item["source_url"]] for item in report_data["ai_news"]])
        report.cybersecurity_news = json.dumps([
            article_ids_by_url[item["source_url"]] for item in report_data["cybersecurity_news"]
        ])
        report.critical_alerts = json.dumps([
            article_ids_by_url[item["source_url"]] for item in report_data["critical_alerts"]
        ])
        report.key_takeaways = json.dumps(report_data["key_takeaways"])
        report.executive_summary = report_data["executive_summary"]
        report.impact_analysis = report_data["impact_analysis"]
        report.sources = json.dumps(report_data["sources"])
        report.warnings = json.dumps(report_data["warnings"])
        report.status = "completed_with_warnings" if report_data["warnings"] else "completed"
        session.commit()
        session.refresh(report)
        return report
    except Exception:
        session.rollback()
        report = session.scalar(select(DailyReport).where(DailyReport.run_key == run_key))
        if report:
            report.status = "failed"
            report.warnings = json.dumps(["Report generation failed; check backend logs."])
            session.commit()
        logger.exception("Daily intelligence report generation failed")
        raise
    finally:
        session.close()
