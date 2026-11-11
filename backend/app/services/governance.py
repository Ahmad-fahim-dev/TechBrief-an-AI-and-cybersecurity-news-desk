import fnmatch
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.models import (
    ActionRequest,
    AgentIdentity,
    ApprovalRequest,
    AuditLog,
    PermissionGrant,
    utc_now,
)

DEFAULT_GRANT_DAYS = 365
APPROVAL_TTL_MINUTES = 15
_AUTHORIZATION_LOCK = threading.RLock()
ACTION_RESOURCES = {
    "workflow.coordinate": ("workflow:*",),
    "source.read": ("sources:*",),
    "article.analyze": ("news:articles",),
    "article.verify": ("news:articles",),
    "article.rank": ("news:articles",),
    "article.summarize": ("news:articles",),
    "image.discover": ("news:images",),
    "report.compose": ("techbrief:reports",),
    "article.write": ("techbrief:articles",),
    "report.write": ("techbrief:reports",),
    "external.ticket.draft": ("external:drafts",),
    "external.incident_report.draft": ("external:drafts",),
    "external.detection_rule.draft": ("external:drafts",),
    "soc.ticket.create": ("external:tickets",),
    "soc.detection_rule.modify": ("soc:detection_rules",),
    "soc.security_config.modify": ("soc:security_config",),
}
APPROVAL_REQUIRED_ACTIONS = {
    "soc.ticket.create",
    "soc.detection_rule.modify",
    "soc.security_config.modify",
}
BUILTIN_AGENTS = {
    "coordinator": {
        "name": "Research Coordinator Agent",
        "role": "workflow_coordinator",
        "tools": ["workflow.coordinate"],
        "grants": {"workflow:*": ["workflow.coordinate"]},
    },
    "ai_researcher": {
        "name": "AI Research Agent",
        "role": "read_only_research",
        "tools": ["source.read", "article.analyze"],
        "grants": {"sources:*": ["source.read"], "news:articles": ["article.analyze"]},
    },
    "cyber_researcher": {
        "name": "Cybersecurity Research Agent",
        "role": "read_only_research",
        "tools": ["source.read", "article.analyze"],
        "grants": {"sources:*": ["source.read"], "news:articles": ["article.analyze"]},
    },
    "source_verifier": {
        "name": "Source Verification Agent",
        "role": "read_only_verification",
        "tools": ["article.verify"],
        "grants": {"news:articles": ["article.verify"]},
    },
    "news_ranker": {
        "name": "News Ranking Agent",
        "role": "read_only_analysis",
        "tools": ["article.rank"],
        "grants": {"news:articles": ["article.rank"]},
    },
    "news_summarizer": {
        "name": "News Summarization Agent",
        "role": "read_only_analysis",
        "tools": ["article.summarize"],
        "grants": {"news:articles": ["article.summarize"]},
    },
    "chief_editor": {
        "name": "Chief Editor Agent",
        "role": "report_editor",
        "tools": ["report.compose"],
        "grants": {"techbrief:reports": ["report.compose"]},
    },
    "image_discovery": {
        "name": "News Image Discovery Agent",
        "role": "read_only_media_discovery",
        "tools": ["image.discover", "source.read"],
        "grants": {"news:images": ["image.discover"], "sources:*": ["source.read"]},
    },
    "report_persistence": {
        "name": "TechBrief Persistence Agent",
        "role": "internal_database_writer",
        "tools": ["article.write", "report.write"],
        "grants": {
            "techbrief:articles": ["article.write"],
            "techbrief:reports": ["report.write"],
        },
    },
    "soc_action_agent": {
        "name": "SOC Action Agent",
        "role": "draft_only_soc",
        "tools": [
            "external.ticket.draft",
            "external.incident_report.draft",
            "external.detection_rule.draft",
            "soc.ticket.create",
            "soc.detection_rule.modify",
            "soc.security_config.modify",
        ],
        "grants": {},
    },
}


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def audit(
    session: Session,
    *,
    action: str,
    resource: str,
    result: str,
    agent_id: str | None = None,
    user_id: str | None = None,
    permission_id: str | None = None,
    approval_reference: str | None = None,
    execution_result: str | None = None,
    failure_reason: str | None = None,
    correlation_id: str | None = None,
) -> AuditLog:
    record = AuditLog(
        id=str(uuid.uuid4()),
        agent_id=agent_id,
        user_id=user_id,
        action=action,
        resource=resource,
        authorization_result=result,
        permission_id=permission_id,
        approval_reference=approval_reference,
        execution_result=execution_result,
        failure_reason=failure_reason,
        timestamp=utc_now(),
        correlation_id=correlation_id or str(uuid.uuid4()),
    )
    session.add(record)
    return record


def ensure_builtin_governance(session_factory: sessionmaker) -> None:
    session = session_factory()
    try:
        now = utc_now()
        for agent_id, definition in BUILTIN_AGENTS.items():
            agent = session.get(AgentIdentity, agent_id)
            if agent is None:
                agent = AgentIdentity(
                    id=agent_id,
                    agent_name=definition["name"],
                    role=definition["role"],
                    status="active",
                    allowed_tools=json.dumps(definition["tools"]),
                )
                session.add(agent)
            else:
                agent.agent_name = definition["name"]
                agent.role = definition["role"]
                agent.allowed_tools = json.dumps(definition["tools"])
            for scope, actions in definition["grants"].items():
                grant_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"techbrief:bootstrap:{agent_id}:{scope}"))
                if session.get(PermissionGrant, grant_id) is not None:
                    continue
                session.add(PermissionGrant(
                    id=grant_id,
                    agent_id=agent_id,
                    granted_by="system:bootstrap",
                    resource_scope=scope,
                    allowed_actions=json.dumps(actions),
                    granted_at=now,
                    expires_at=now + timedelta(days=DEFAULT_GRANT_DAYS),
                    status="active",
                    approval_reference="bootstrap:least-privilege",
                ))
                audit(
                    session,
                    agent_id=agent_id,
                    action="permission.bootstrap",
                    resource=scope,
                    result="ALLOW",
                    permission_id=grant_id,
                    execution_result="Built-in least-privilege grant created",
                )
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def authorize_in_session(
    session: Session,
    *,
    agent_id: str,
    action: str,
    resource: str,
    user_id: str | None = None,
    correlation_id: str | None = None,
) -> PermissionGrant | None:
    agent = session.get(AgentIdentity, agent_id)
    reason = None
    grant_match = None
    patterns = ACTION_RESOURCES.get(action)
    if not patterns:
        reason = "Action is not registered in the deterministic policy"
    elif not any(fnmatch.fnmatchcase(resource, pattern) for pattern in patterns):
        reason = "Resource is outside the registered action scope"
    elif agent is None or agent.status != "active":
        reason = "Agent identity is missing or inactive"
    elif action not in json.loads(agent.allowed_tools):
        reason = "Agent identity is not allowed to use this action"
    else:
        now = utc_now()
        grants = session.scalars(
            select(PermissionGrant).where(
                PermissionGrant.agent_id == agent_id,
                PermissionGrant.status == "active",
                PermissionGrant.revoked_at.is_(None),
                PermissionGrant.expires_at > now,
            )
        ).all()
        for grant in grants:
            if action in json.loads(grant.allowed_actions) and fnmatch.fnmatchcase(resource, grant.resource_scope):
                grant_match = grant
                break
        if grant_match is None:
            reason = "No active, unexpired grant covers this action and resource"
    audit(
        session,
        agent_id=agent_id,
        user_id=user_id,
        action=action,
        resource=resource,
        result="ALLOW" if grant_match else "DENY",
        permission_id=grant_match.id if grant_match else None,
        failure_reason=reason,
        correlation_id=correlation_id,
    )
    return grant_match


def authorize_agent_action(
    session_factory: sessionmaker,
    *,
    agent_id: str,
    action: str,
    resource: str,
    correlation_id: str | None = None,
) -> bool:
    with _AUTHORIZATION_LOCK:
        session = session_factory()
        try:
            grant = authorize_in_session(
                session,
                agent_id=agent_id,
                action=action,
                resource=resource,
                correlation_id=correlation_id,
            )
            session.commit()
            return grant is not None
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def expire_pending_approvals(session: Session) -> None:
    now = utc_now()
    pending = session.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.decision.in_(("pending", "approved")),
            ApprovalRequest.expires_at <= now,
        )
    ).all()
    for approval in pending:
        previous_decision = approval.decision
        approval.decision = "expired"
        approval.decision_reason = (
            "Approval window elapsed without an explicit decision"
            if previous_decision == "pending"
            else "Approved action authorization expired before execution"
        )
        approval.decided_at = now
        action_request = session.get(ActionRequest, approval.action_request_id)
        if action_request:
            action_request.status = "Expired"
        audit(
            session,
            action=action_request.requested_action if action_request else "approval.expire",
            resource=action_request.target_resource if action_request else "approval",
            result="EXPIRED",
            agent_id=action_request.agent_id if action_request else None,
            approval_reference=approval.id,
            failure_reason=approval.decision_reason,
            correlation_id=action_request.correlation_id if action_request else None,
        )
