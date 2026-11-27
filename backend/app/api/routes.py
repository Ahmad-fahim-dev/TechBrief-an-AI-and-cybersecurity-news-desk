import json
import logging
import asyncio
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.app.agents.image_discovery import discover_article_images
from backend.app.models import (
    ActionRequest,
    AgentIdentity,
    ApprovalRequest,
    AppSettings,
    Article,
    AuditLog,
    Bookmark,
    DailyReport,
    PermissionGrant,
    utc_now,
)
from backend.app.schemas import (
    ActionRequestCreate,
    ApprovalDecision,
    ArticleOut,
    BookmarkCreate,
    PermissionGrantCreate,
    PermissionRevoke,
    SettingsUpdate,
)
from backend.app.services.collector import configured_feeds
from backend.app.services.governance import (
    APPROVAL_TTL_MINUTES,
    ACTION_RESOURCES,
    APPROVAL_REQUIRED_ACTIONS,
    audit,
    authorize_in_session,
    expire_pending_approvals,
)
from backend.app.services.mock_integrations import MockSocAdapter
from backend.app.services.report_service import generate_report

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
IMAGE_FIELDS = (
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
)
DRAFT_ACTIONS = {
    "external.ticket.draft",
    "external.incident_report.draft",
    "external.detection_rule.draft",
}
EXTERNAL_ACTIONS = {
    action for action in ACTION_RESOURCES
    if action.startswith(("external.", "soc."))
}


def _session(request: Request) -> Session:
    return request.app.state.session_factory()


def _escape_like(term: str) -> str:
    """Escape SQL LIKE wildcards so search terms cannot inject %/_ patterns."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _require_admin(request: Request, authorization: str | None) -> str:
    expected = request.app.state.config.admin_api_token
    if not expected:
        raise HTTPException(status_code=503, detail="Governance admin access is not configured")
    scheme, separator, supplied = (authorization or "").partition(" ")
    if (
        not separator
        or scheme.lower() != "bearer"
        or not hmac.compare_digest(supplied, expected)
    ):
        session = _session(request)
        try:
            audit(
                session,
                action="admin.authenticate",
                resource=request.url.path,
                result="DENY",
                failure_reason="Missing or invalid administrator bearer token",
            )
            session.commit()
        finally:
            session.close()
        raise HTTPException(status_code=401, detail="Valid admin bearer token required")
    return request.app.state.config.admin_user_id


def _agent_out(agent: AgentIdentity) -> dict[str, Any]:
    return {
        "id": agent.id,
        "agent_name": agent.agent_name,
        "role": agent.role,
        "status": agent.status,
        "allowed_tools": json.loads(agent.allowed_tools),
        "created_at": agent.created_at,
    }


def _grant_status(grant: PermissionGrant) -> str:
    if grant.revoked_at or grant.status == "revoked":
        return "revoked"
    expiry = grant.expires_at
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= utc_now():
        return "expired"
    return grant.status


def _grant_out(grant: PermissionGrant) -> dict[str, Any]:
    return {
        "id": grant.id,
        "agent_id": grant.agent_id,
        "granted_by": grant.granted_by,
        "resource_scope": grant.resource_scope,
        "allowed_actions": json.loads(grant.allowed_actions),
        "granted_at": grant.granted_at,
        "expires_at": grant.expires_at,
        "revoked_at": grant.revoked_at,
        "status": _grant_status(grant),
        "approval_reference": grant.approval_reference,
    }


def _action_out(action: ActionRequest) -> dict[str, Any]:
    return {
        "id": action.id,
        "agent_id": action.agent_id,
        "requested_action": action.requested_action,
        "target_resource": action.target_resource,
        "reason": action.reason,
        "permission_id": action.permission_id,
        "requested_by": action.requested_by,
        "status": action.status,
        "created_at": action.created_at,
        "correlation_id": action.correlation_id,
        "execution_result": json.loads(action.execution_result) if action.execution_result else None,
        "failure_reason": action.failure_reason,
    }


def _approval_out(approval: ApprovalRequest) -> dict[str, Any]:
    return {
        "id": approval.id,
        "action_request_id": approval.action_request_id,
        "requested_by": approval.requested_by,
        "approved_by": approval.approved_by,
        "decision": approval.decision,
        "decision_reason": approval.decision_reason,
        "expires_at": approval.expires_at,
        "decided_at": approval.decided_at,
    }

def _report_out(report: DailyReport) -> dict[str, Any]:
    return {
        "id": report.id,
        "report_date": report.report_date,
        "executive_summary": report.executive_summary,
        "ai_news": json.loads(report.ai_news),
        "cybersecurity_news": json.loads(report.cybersecurity_news),
        "critical_alerts": json.loads(report.critical_alerts),
        "key_takeaways": json.loads(report.key_takeaways),
        "impact_analysis": report.impact_analysis,
        "sources": json.loads(report.sources),
        "status": report.status,
        "warnings": json.loads(report.warnings),
        "created_at": report.created_at,
    }


def _settings_out(options: AppSettings) -> dict[str, Any]:
    return {
        "timezone": options.timezone,
        "scheduled_time": options.scheduled_time,
        "schedule_enabled": options.schedule_enabled,
        "enabled_categories": json.loads(options.enabled_categories),
        "articles_per_category": options.articles_per_category,
        "preferred_sources": json.loads(options.preferred_sources),
    }


@router.get("/dashboard")
def dashboard(request: Request) -> dict[str, Any]:
    session = _session(request)
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        total = session.scalar(select(func.count()).select_from(Article).where(Article.published_at >= cutoff)) or 0
        ai_count = session.scalar(select(func.count()).select_from(Article).where(
            Article.category == "ai", Article.published_at >= cutoff
        )) or 0
        cyber_count = session.scalar(select(func.count()).select_from(Article).where(
            Article.category == "cybersecurity", Article.published_at >= cutoff
        )) or 0
        critical_count = session.scalar(select(func.count()).select_from(Article).where(
            Article.category == "cybersecurity",
            Article.severity == "Critical",
            Article.published_at >= cutoff,
        )) or 0
        latest = session.scalar(select(DailyReport).where(
            DailyReport.status.in_(("completed", "completed_with_warnings"))
        ).order_by(DailyReport.created_at.desc()).limit(1))
        settings = session.get(AppSettings, 1)
        scheduler = request.app.state.scheduler
        next_run = scheduler.get_job("daily-report").next_run_time if scheduler and scheduler.get_job("daily-report") else None
        top = session.scalars(
            select(Article).where(Article.published_at >= cutoff).order_by(Article.importance_score.desc()).limit(6)
        ).all()
        return {
            "product": "TechBref",
            "total_articles": total,
            "ai_news_count": ai_count,
            "cybersecurity_news_count": cyber_count,
            "critical_alerts_count": critical_count,
            "last_updated": latest.created_at if latest else None,
            "next_scheduled_report": next_run,
            "latest_report_id": latest.id if latest else None,
            "top_stories": [ArticleOut.model_validate(article).model_dump(mode="json") for article in top],
            "schedule": _settings_out(settings) if settings else None,
        }
    finally:
        session.close()


@router.get("/news")
def list_news(
    request: Request,
    q: str | None = Query(default=None, max_length=200),
    category: str | None = Query(default=None, pattern="^(ai|cybersecurity)$"),
    source: str | None = Query(default=None, max_length=180),
    from_date: datetime | None = None,
    to_date: datetime | None = None,
    min_importance: float = Query(default=0, ge=0, le=10),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    session = _session(request)
    try:
        query = select(Article)
        filters = [Article.importance_score >= min_importance]
        if q:
            term = f"%{_escape_like(q.strip())}%"
            filters.append(or_(
                Article.title.ilike(term, escape="\\"),
                Article.description.ilike(term, escape="\\"),
                Article.summary.ilike(term, escape="\\"),
            ))
        if category:
            filters.append(Article.category == category)
        if source:
            filters.append(Article.source_name == source)
        if from_date:
            filters.append(Article.published_at >= from_date)
        if to_date:
            filters.append(Article.published_at <= to_date)
        total = session.scalar(select(func.count()).select_from(Article).where(*filters)) or 0
        articles = session.scalars(
            query.where(*filters).order_by(Article.published_at.desc(), Article.importance_score.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()
        return {"items": [ArticleOut.model_validate(item).model_dump(mode="json") for item in articles],
                "page": page, "page_size": page_size, "total": total}
    finally:
        session.close()


@router.get("/news/{article_id}", response_model=ArticleOut)
def get_news(article_id: int, request: Request) -> Article:
    session = _session(request)
    try:
        article = session.get(Article, article_id)
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        return article
    finally:
        session.close()


@router.post("/images/discover-missing")
async def discover_missing_images(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    # Spends external fetch budget and rewrites stored article image data;
    # restricted to the configured administrator like other operator actions.
    _require_admin(request, authorization)
    session = _session(request)
    try:
        image_grant = authorize_in_session(
            session,
            agent_id="image_discovery",
            action="image.discover",
            resource="news:images",
            correlation_id=str(uuid.uuid4()),
        )
        if image_grant is None:
            session.commit()
            raise HTTPException(status_code=403, detail="Image discovery agent lacks an active permission")
        articles = session.scalars(
            select(Article)
            .where(Article.image_checked_at.is_(None))
            .order_by(Article.published_at.desc())
            .limit(30)
        ).all()
        inputs = [
            {
                "title": article.title,
                "category": article.category,
                "source_name": article.source_name,
                "source_url": article.source_url,
                "image_candidates": [],
            }
            for article in articles
        ]
        article_ids = [article.id for article in articles]
        session.commit()
    finally:
        session.close()

    if not inputs:
        return {"updated": 0}
    read_session = _session(request)
    try:
        source_grant = authorize_in_session(
            read_session,
            agent_id="image_discovery",
            action="source.read",
            resource="sources:article-pages",
        )
        read_session.commit()
        if source_grant is None:
            raise HTTPException(status_code=403, detail="Image discovery agent lacks source-read permission")
    finally:
        read_session.close()
    discovered = await discover_article_images(inputs)
    session = _session(request)
    try:
        for article_id, image in zip(article_ids, discovered, strict=True):
            article = session.get(Article, article_id)
            if article is None:
                continue
            for field in IMAGE_FIELDS:
                setattr(article, field, image[field])
        session.commit()
        return {"updated": len(discovered)}
    except Exception:
        session.rollback()
        logger.exception("Could not save discovered images for existing articles")
        raise
    finally:
        session.close()


@router.get("/reports")
def list_reports(request: Request, page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100)):
    session = _session(request)
    try:
        total = session.scalar(select(func.count()).select_from(DailyReport)) or 0
        reports = session.scalars(
            select(DailyReport).order_by(DailyReport.created_at.desc())
            .offset((page - 1) * page_size).limit(page_size)
        ).all()
        return {"items": [_report_out(item) for item in reports], "page": page, "page_size": page_size, "total": total}
    finally:
        session.close()


@router.get("/reports/latest")
def latest_report(request: Request):
    session = _session(request)
    try:
        report = session.scalar(select(DailyReport).where(
            DailyReport.status.in_(("completed", "completed_with_warnings"))
        )
                                .order_by(DailyReport.created_at.desc()).limit(1))
        if not report:
            raise HTTPException(status_code=404, detail="No completed report is available yet")
        return _report_out(report)
    finally:
        session.close()


@router.get("/reports/status")
def generation_status(request: Request) -> dict[str, Any]:
    return dict(request.app.state.job)


@router.get("/reports/{report_id}")
def get_report(report_id: int, request: Request):
    session = _session(request)
    try:
        report = session.get(DailyReport, report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        output = _report_out(report)
        ids = list(dict.fromkeys(output["ai_news"] + output["cybersecurity_news"]))
        stories = session.scalars(select(Article).where(Article.id.in_(ids))).all() if ids else []
        output["stories"] = [ArticleOut.model_validate(article).model_dump(mode="json") for article in stories]
        return output
    finally:
        session.close()


@router.post("/reports/generate", status_code=status.HTTP_202_ACCEPTED)
async def start_generation(
    request: Request,
    authorization: str | None = Header(default=None),
):
    # Report generation spends RSS/Tavily/OpenAI provider credits; require the
    # administrator token so anonymous visitors cannot drain the budget.
    _require_admin(request, authorization)
    current = request.app.state.job
    if current["status"] == "running":
        raise HTTPException(status_code=409, detail="A report is already being generated")
    request.app.state.job = {"status": "running", "started_at": datetime.now(timezone.utc), "error": None}

    def update_stage(stage: str) -> None:
        request.app.state.job = {**request.app.state.job, "stage": stage}

    async def run() -> None:
        try:
            report = await generate_report(
                request.app.state.session_factory,
                request.app.state.config,
                on_progress=update_stage,
            )
            request.app.state.job = {
                "status": report.status,
                "report_id": report.id,
                "finished_at": datetime.now(timezone.utc),
                "stage": request.app.state.job.get("stage"),
                "error": None,
            }
        except Exception:
            logger.exception("Manual report generation failed")
            request.app.state.job = {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc),
                "error": "Report generation failed; check backend logs.",
            }

    request.app.state.job_task = asyncio.create_task(run())
    return {"status": "running", "message": "Research has started"}


@router.post("/reports/{report_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_report(
    report_id: int,
    request: Request,
    authorization: str | None = Header(default=None),
):
    _require_admin(request, authorization)
    if request.app.state.job["status"] == "running":
        raise HTTPException(status_code=409, detail="A report is already being generated")
    session = _session(request)
    try:
        report = session.get(DailyReport, report_id)
        if not report:
            raise HTTPException(status_code=404, detail="Report not found")
        if report.status != "failed":
            raise HTTPException(status_code=409, detail="Only failed reports can be retried")
    finally:
        session.close()

    request.app.state.job = {"status": "running", "started_at": datetime.now(timezone.utc), "error": None}

    def update_stage(stage: str) -> None:
        request.app.state.job = {**request.app.state.job, "stage": stage}

    async def run() -> None:
        try:
            retry = await generate_report(
                request.app.state.session_factory,
                request.app.state.config,
                run_key=f"retry:{report_id}:{uuid.uuid4()}",
                on_progress=update_stage,
            )
            request.app.state.job = {
                "status": retry.status,
                "report_id": retry.id,
                "finished_at": datetime.now(timezone.utc),
                "stage": request.app.state.job.get("stage"),
                "error": None,
            }
        except Exception:
            logger.exception("Report retry failed")
            request.app.state.job = {
                "status": "failed",
                "finished_at": datetime.now(timezone.utc),
                "error": "Report generation failed; check backend logs.",
            }

    request.app.state.job_task = asyncio.create_task(run())
    return {"status": "running", "message": "Report retry started"}


@router.get("/settings")
def get_settings(request: Request):
    session = _session(request)
    try:
        options = session.get(AppSettings, 1)
        return _settings_out(options) if options else _settings_out(_create_defaults(session, request))
    finally:
        session.close()


def _create_defaults(session: Session, request: Request) -> AppSettings:
    config = request.app.state.config
    options = AppSettings(
        id=1,
        timezone=config.timezone,
        scheduled_time=config.scheduled_time,
        schedule_enabled=config.schedule_enabled,
        articles_per_category=config.articles_per_category,
    )
    session.add(options)
    session.commit()
    return options


@router.put("/settings")
def update_settings(
    data: SettingsUpdate,
    request: Request,
    authorization: str | None = Header(default=None),
):
    # Settings control the scheduler and feed selection; changes are operator
    # actions and require the administrator token.
    _require_admin(request, authorization)
    session = _session(request)
    try:
        known_sources = {feed["name"] for feed in configured_feeds(request.app.state.config)}
        unknown_sources = set(data.preferred_sources) - known_sources
        if unknown_sources:
            raise HTTPException(status_code=422, detail="One or more preferred sources are not configured")
        options = session.get(AppSettings, 1) or _create_defaults(session, request)
        options.timezone = data.timezone
        options.scheduled_time = data.scheduled_time
        options.schedule_enabled = data.schedule_enabled
        options.enabled_categories = json.dumps(data.enabled_categories)
        options.articles_per_category = data.articles_per_category
        options.preferred_sources = json.dumps(data.preferred_sources)
        session.commit()
        scheduler = request.app.state.scheduler
        if scheduler:
            request.app.state.refresh_schedule()
        return _settings_out(options)
    finally:
        session.close()


@router.get("/sources")
def list_sources(request: Request):
    session = _session(request)
    try:
        rows = session.execute(select(Article.source_name, Article.category, func.count(Article.id))
                               .group_by(Article.source_name, Article.category)).all()
        counts = {(name, category): count for name, category, count in rows}
        options = session.get(AppSettings, 1)
        preferred = json.loads(options.preferred_sources) if options else []
        items = []
        for feed in configured_feeds(request.app.state.config):
            items.append({
                "name": feed["name"],
                "category": feed["category"],
                "article_count": counts.get((feed["name"], feed["category"]), 0),
                "enabled": not preferred or feed["name"] in preferred,
            })
        return {"items": items}
    finally:
        session.close()


@router.post("/bookmarks", status_code=status.HTTP_201_CREATED)
def create_bookmark(data: BookmarkCreate, request: Request):
    session = _session(request)
    try:
        article = session.get(Article, data.article_id)
        if not article:
            raise HTTPException(status_code=404, detail="Article not found")
        bookmark = session.scalar(select(Bookmark).where(Bookmark.article_id == article.id))
        if bookmark:
            return {"id": bookmark.id, "article_id": article.id}
        bookmark = Bookmark(article_id=article.id)
        session.add(bookmark)
        session.commit()
        return {"id": bookmark.id, "article_id": article.id}
    except IntegrityError:
        session.rollback()
        raise HTTPException(status_code=409, detail="Article is already bookmarked")
    finally:
        session.close()


@router.get("/bookmarks")
def list_bookmarks(request: Request):
    session = _session(request)
    try:
        rows = session.scalars(select(Bookmark).order_by(Bookmark.created_at.desc())).all()
        return {"items": [{"id": row.id, "created_at": row.created_at,
                           "article": ArticleOut.model_validate(row.article).model_dump(mode="json")}
                          for row in rows]}
    finally:
        session.close()


@router.delete("/bookmarks/{bookmark_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bookmark(bookmark_id: int, request: Request):
    session = _session(request)
    try:
        bookmark = session.get(Bookmark, bookmark_id)
        if not bookmark:
            raise HTTPException(status_code=404, detail="Bookmark not found")
        session.delete(bookmark)
        session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    finally:
        session.close()


@router.get("/governance/session")
def governance_session(request: Request, authorization: str | None = Header(default=None)):
    user_id = _require_admin(request, authorization)
    return {"user_id": user_id, "role": "admin"}


@router.get("/agents")
def list_agents(request: Request, authorization: str | None = Header(default=None)):
    _require_admin(request, authorization)
    session = _session(request)
    try:
        agents = session.scalars(select(AgentIdentity).order_by(AgentIdentity.agent_name)).all()
        return {"items": [_agent_out(agent) for agent in agents]}
    finally:
        session.close()


@router.get("/agents/{agent_id}")
def get_agent(agent_id: str, request: Request, authorization: str | None = Header(default=None)):
    _require_admin(request, authorization)
    session = _session(request)
    try:
        agent = session.get(AgentIdentity, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="Agent identity not found")
        output = _agent_out(agent)
        grants = session.scalars(
            select(PermissionGrant).where(PermissionGrant.agent_id == agent_id)
            .order_by(PermissionGrant.granted_at.desc())
        ).all()
        output["permissions"] = [_grant_out(grant) for grant in grants]
        return output
    finally:
        session.close()


@router.get("/permissions")
def list_permissions(
    request: Request,
    authorization: str | None = Header(default=None),
    agent_id: str | None = None,
):
    _require_admin(request, authorization)
    session = _session(request)
    try:
        expire_pending_approvals(session)
        query = select(PermissionGrant).order_by(PermissionGrant.granted_at.desc())
        if agent_id:
            query = query.where(PermissionGrant.agent_id == agent_id)
        grants = session.scalars(query.limit(500)).all()
        session.commit()
        return {"items": [_grant_out(grant) for grant in grants]}
    finally:
        session.close()


@router.post("/permissions/grant", status_code=status.HTTP_201_CREATED)
def create_permission_grant(
    data: PermissionGrantCreate,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user_id = _require_admin(request, authorization)
    session = _session(request)
    try:
        agent = session.get(AgentIdentity, data.agent_id)
        accepted = (
            agent is not None
            and agent.status == "active"
            and set(data.allowed_actions).issubset(set(json.loads(agent.allowed_tools)))
            and all(action in ACTION_RESOURCES for action in data.allowed_actions)
            and (
                not set(data.allowed_actions).intersection(EXTERNAL_ACTIONS)
                or bool(data.approval_reference and data.approval_reference.strip())
            )
            and all(
                data.resource_scope in ACTION_RESOURCES[action]
                for action in data.allowed_actions
            )
        )
        correlation_id = str(uuid.uuid4())
        if not accepted:
            audit(
                session,
                agent_id=data.agent_id if agent else None,
                user_id=user_id,
                action="permission.grant",
                resource=data.resource_scope,
                result="DENY",
                failure_reason="Agent, allowed action, or resource scope failed policy validation",
                correlation_id=correlation_id,
            )
            session.commit()
            raise HTTPException(status_code=403, detail="Grant exceeds the agent's registered policy")
        now = utc_now()
        grant = PermissionGrant(
            id=str(uuid.uuid4()),
            agent_id=data.agent_id,
            granted_by=user_id,
            resource_scope=data.resource_scope,
            allowed_actions=json.dumps(data.allowed_actions),
            granted_at=now,
            expires_at=now + timedelta(minutes=data.expires_in_minutes),
            status="active",
            approval_reference=data.approval_reference,
        )
        session.add(grant)
        audit(
            session,
            agent_id=data.agent_id,
            user_id=user_id,
            action="permission.grant",
            resource=data.resource_scope,
            result="ALLOW",
            permission_id=grant.id,
            execution_result=f"Granted for {data.expires_in_minutes} minutes",
            correlation_id=correlation_id,
        )
        session.commit()
        return _grant_out(grant)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.post("/permissions/revoke")
def revoke_permission(
    data: PermissionRevoke,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user_id = _require_admin(request, authorization)
    session = _session(request)
    try:
        grant = session.get(PermissionGrant, data.permission_id)
        if grant is None:
            raise HTTPException(status_code=404, detail="Permission grant not found")
        if _grant_status(grant) == "revoked":
            raise HTTPException(status_code=409, detail="Permission grant is already revoked")
        grant.status = "revoked"
        grant.revoked_at = utc_now()
        audit(
            session,
            agent_id=grant.agent_id,
            user_id=user_id,
            action="permission.revoke",
            resource=grant.resource_scope,
            result="ALLOW",
            permission_id=grant.id,
            execution_result="Permission revoked immediately",
            failure_reason=data.reason,
        )
        session.commit()
        return _grant_out(grant)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.get("/actions")
def list_actions(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    _require_admin(request, authorization)
    session = _session(request)
    try:
        expire_pending_approvals(session)
        actions = session.scalars(
            select(ActionRequest).order_by(ActionRequest.created_at.desc()).limit(limit)
        ).all()
        session.commit()
        return {"items": [_action_out(action) for action in actions]}
    finally:
        session.close()


@router.post("/actions/request", status_code=status.HTTP_201_CREATED)
def create_action_request(
    data: ActionRequestCreate,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user_id = _require_admin(request, authorization)
    session = _session(request)
    try:
        agent = session.get(AgentIdentity, data.agent_id)
        correlation_id = str(uuid.uuid4())
        if agent is None:
            audit(
                session,
                user_id=user_id,
                action=data.requested_action,
                resource=data.target_resource,
                result="DENY",
                failure_reason="Unknown agent identity",
                correlation_id=correlation_id,
            )
            session.commit()
            raise HTTPException(status_code=404, detail="Agent identity not found")
        grant = authorize_in_session(
            session,
            agent_id=data.agent_id,
            action=data.requested_action,
            resource=data.target_resource,
            user_id=user_id,
            correlation_id=correlation_id,
        )
        is_allowed = grant is not None
        is_approval_required = data.requested_action in APPROVAL_REQUIRED_ACTIONS
        action_status = (
            "Rejected" if not is_allowed else
            "Pending Approval" if is_approval_required else
            "Approved"
        )
        action = ActionRequest(
            id=str(uuid.uuid4()),
            agent_id=data.agent_id,
            requested_action=data.requested_action,
            target_resource=data.target_resource,
            reason=data.reason,
            permission_id=grant.id if grant else None,
            requested_by=data.agent_id,
            status=action_status,
            created_at=utc_now(),
            correlation_id=correlation_id,
            failure_reason=None if is_allowed else "Default-deny policy: no valid scoped permission",
        )
        session.add(action)
        if is_allowed and is_approval_required:
            session.add(ApprovalRequest(
                id=str(uuid.uuid4()),
                action_request_id=action.id,
                requested_by=data.agent_id,
                decision="pending",
                expires_at=utc_now() + timedelta(minutes=APPROVAL_TTL_MINUTES),
            ))
        audit(
            session,
            agent_id=data.agent_id,
            user_id=user_id,
            action=data.requested_action,
            resource=data.target_resource,
            result="ALLOW" if is_allowed else "DENY",
            permission_id=grant.id if grant else None,
            execution_result=action_status,
            failure_reason=action.failure_reason,
            correlation_id=correlation_id,
        )
        session.commit()
        return _action_out(action)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.post("/actions/{action_id}/execute")
def execute_action(
    action_id: str,
    request: Request,
    authorization: str | None = Header(default=None),
):
    user_id = _require_admin(request, authorization)
    session = _session(request)
    try:
        expire_pending_approvals(session)
        action = session.get(ActionRequest, action_id)
        if action is None:
            raise HTTPException(status_code=404, detail="Action request not found")
        if action.status != "Approved":
            audit(
                session,
                agent_id=action.agent_id,
                user_id=user_id,
                action=action.requested_action,
                resource=action.target_resource,
                result="DENY",
                permission_id=action.permission_id,
                execution_result="Execution refused",
                failure_reason="Action is not in approved state",
                correlation_id=action.correlation_id,
            )
            session.commit()
            raise HTTPException(status_code=409, detail="Only approved, unexpired actions can execute")
        if action.requested_action in APPROVAL_REQUIRED_ACTIONS:
            approval = session.scalar(
                select(ApprovalRequest).where(ApprovalRequest.action_request_id == action.id)
            )
            execution_grant = session.get(PermissionGrant, action.permission_id) if action.permission_id else None
            if (
                approval is None
                or approval.decision != "approved"
                or execution_grant is None
                or _grant_status(execution_grant) != "active"
            ):
                action.status = "Expired" if approval and approval.decision == "expired" else "Rejected"
                action.failure_reason = "Approval expired or was not valid at execution time"
                audit(
                    session,
                    agent_id=action.agent_id,
                    user_id=user_id,
                    action=action.requested_action,
                    resource=action.target_resource,
                    result="DENY",
                    permission_id=action.permission_id,
                    approval_reference=approval.id if approval else None,
                    failure_reason=action.failure_reason,
                    correlation_id=action.correlation_id,
                )
                session.commit()
                raise HTTPException(status_code=403, detail=action.failure_reason)
        grant = authorize_in_session(
            session,
            agent_id=action.agent_id,
            action=action.requested_action,
            resource=action.target_resource,
            user_id=user_id,
            correlation_id=action.correlation_id,
        )
        if grant is None:
            action.status = "Revoked"
            action.failure_reason = "Permission expired or was revoked before execution"
            audit(
                session,
                agent_id=action.agent_id,
                user_id=user_id,
                action=action.requested_action,
                resource=action.target_resource,
                result="DENY",
                failure_reason=action.failure_reason,
                correlation_id=action.correlation_id,
            )
            session.commit()
            raise HTTPException(status_code=403, detail=action.failure_reason)
        if action.requested_action not in DRAFT_ACTIONS:
            action.status = "Failed"
            action.failure_reason = "No external executor is configured; sensitive SOC execution is disabled"
            audit(
                session,
                agent_id=action.agent_id,
                user_id=user_id,
                action=action.requested_action,
                resource=action.target_resource,
                result="DENY",
                permission_id=grant.id,
                execution_result="FAILED",
                failure_reason=action.failure_reason,
                correlation_id=action.correlation_id,
            )
            session.commit()
            return _action_out(action)
        result = MockSocAdapter().create_draft(
            action.requested_action,
            action.target_resource,
            action.reason,
        )
        action.execution_result = json.dumps(result)
        action.status = "Completed"
        action.failure_reason = None
        audit(
            session,
            agent_id=action.agent_id,
            user_id=user_id,
            action=action.requested_action,
            resource=action.target_resource,
            result="ALLOW",
            permission_id=grant.id,
            execution_result="Draft created internally; no external system contacted",
            correlation_id=action.correlation_id,
        )
        session.commit()
        return _action_out(action)
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.get("/approvals")
def list_approvals(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = Query(default=100, ge=1, le=500),
):
    _require_admin(request, authorization)
    session = _session(request)
    try:
        expire_pending_approvals(session)
        approvals = session.scalars(
            select(ApprovalRequest).order_by(ApprovalRequest.expires_at.desc()).limit(limit)
        ).all()
        session.commit()
        items = []
        for approval in approvals:
            output = _approval_out(approval)
            action = session.get(ActionRequest, approval.action_request_id)
            output["action"] = _action_out(action) if action else None
            items.append(output)
        return {"items": items}
    finally:
        session.close()


def _decide_approval(
    approval_id: str,
    data: ApprovalDecision,
    request: Request,
    authorization: str | None,
    decision: str,
) -> dict[str, Any]:
    user_id = _require_admin(request, authorization)
    session = _session(request)
    try:
        expire_pending_approvals(session)
        approval = session.get(ApprovalRequest, approval_id)
        if approval is None:
            raise HTTPException(status_code=404, detail="Approval request not found")
        if approval.decision != "pending":
            audit(
                session,
                agent_id=approval.requested_by,
                user_id=user_id,
                action="approval.decision",
                resource=approval.action_request_id,
                result="DENY",
                approval_reference=approval.id,
                failure_reason="Approval is no longer pending",
            )
            session.commit()
            raise HTTPException(status_code=409, detail="Approval is no longer pending")
        action = session.get(ActionRequest, approval.action_request_id)
        if action is None:
            raise HTTPException(status_code=404, detail="Requested action not found")
        if user_id == action.agent_id or user_id == approval.requested_by:
            audit(
                session,
                agent_id=action.agent_id,
                user_id=user_id,
                action=action.requested_action,
                resource=action.target_resource,
                result="DENY",
                permission_id=action.permission_id,
                approval_reference=approval.id,
                failure_reason="An agent cannot approve its own action",
                correlation_id=action.correlation_id,
            )
            session.commit()
            raise HTTPException(status_code=403, detail="An agent cannot approve its own action")
        now = utc_now()
        if decision == "approved":
            grant = authorize_in_session(
                session,
                agent_id=action.agent_id,
                action=action.requested_action,
                resource=action.target_resource,
                user_id=user_id,
                correlation_id=action.correlation_id,
            )
            if grant is None:
                approval.decision = "rejected"
                approval.approved_by = user_id
                approval.decision_reason = "Permission expired or was revoked before approval"
                approval.decided_at = now
                action.status = "Rejected"
                action.failure_reason = approval.decision_reason
                audit(
                    session,
                    agent_id=action.agent_id,
                    user_id=user_id,
                    action=action.requested_action,
                    resource=action.target_resource,
                    result="DENY",
                    approval_reference=approval.id,
                    failure_reason=approval.decision_reason,
                    correlation_id=action.correlation_id,
                )
                session.commit()
                return {"approval": _approval_out(approval), "action": _action_out(action)}
            action.permission_id = grant.id
        approval.decision = decision
        approval.approved_by = user_id
        approval.decision_reason = data.reason
        approval.decided_at = now
        if decision == "approved":
            approval.expires_at = now + timedelta(minutes=APPROVAL_TTL_MINUTES)
        action.status = "Approved" if decision == "approved" else "Rejected"
        if decision == "rejected":
            action.failure_reason = data.reason
        audit(
            session,
            agent_id=action.agent_id,
            user_id=user_id,
            action=action.requested_action,
            resource=action.target_resource,
            result="ALLOW" if decision == "approved" else "DENY",
            permission_id=action.permission_id,
            approval_reference=approval.id,
            execution_result=action.status,
            failure_reason=None if decision == "approved" else data.reason,
            correlation_id=action.correlation_id,
        )
        session.commit()
        return {"approval": _approval_out(approval), "action": _action_out(action)}
    except HTTPException:
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@router.post("/approvals/{approval_id}/approve")
def approve_action(
    approval_id: str,
    data: ApprovalDecision,
    request: Request,
    authorization: str | None = Header(default=None),
):
    return _decide_approval(approval_id, data, request, authorization, "approved")


@router.post("/approvals/{approval_id}/reject")
def reject_action(
    approval_id: str,
    data: ApprovalDecision,
    request: Request,
    authorization: str | None = Header(default=None),
):
    return _decide_approval(approval_id, data, request, authorization, "rejected")


@router.get("/audit-logs")
def list_audit_logs(
    request: Request,
    authorization: str | None = Header(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    agent_id: str | None = None,
):
    _require_admin(request, authorization)
    session = _session(request)
    try:
        query = select(AuditLog).order_by(AuditLog.timestamp.desc())
        if agent_id:
            query = query.where(AuditLog.agent_id == agent_id)
        records = session.scalars(query.limit(limit)).all()
        return {
            "items": [{
                "id": record.id,
                "timestamp": record.timestamp,
                "agent_id": record.agent_id,
                "user_id": record.user_id,
                "action": record.action,
                "resource": record.resource,
                "authorization_result": record.authorization_result,
                "permission_id": record.permission_id,
                "approval_reference": record.approval_reference,
                "execution_result": record.execution_result,
                "failure_reason": record.failure_reason,
                "correlation_id": record.correlation_id,
            } for record in records]
        }
    finally:
        session.close()
