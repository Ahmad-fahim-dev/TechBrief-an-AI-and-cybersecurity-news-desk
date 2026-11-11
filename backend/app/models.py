from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("source_url", name="uq_articles_source_url"),
        Index("ix_articles_category_published", "category", "published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    category: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_name: Mapped[str] = mapped_column(String(180), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    score_reasons: Mapped[str] = mapped_column(Text, default="", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(40), default="unverified", nullable=False)
    impact_analysis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    severity: Mapped[str | None] = mapped_column(String(20))
    cve_ids: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text)
    image_alt: Mapped[str | None] = mapped_column(Text)
    image_credit: Mapped[str | None] = mapped_column(String(300))
    image_source_name: Mapped[str | None] = mapped_column(String(180))
    image_source_url: Mapped[str | None] = mapped_column(Text)
    image_license: Mapped[str | None] = mapped_column(String(300))
    image_type: Mapped[str] = mapped_column(String(32), default="category_fallback", nullable=False)
    is_illustrative: Mapped[bool] = mapped_column(default=True, nullable=False)
    image_verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    image_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bookmarks: Mapped[list["Bookmark"]] = relationship(back_populates="article", cascade="all, delete-orphan")


class DailyReport(Base):
    __tablename__ = "daily_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_key: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    report_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    executive_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    ai_news: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    cybersecurity_news: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    critical_alerts: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    key_takeaways: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    impact_analysis: Mapped[str] = mapped_column(Text, default="", nullable=False)
    sources: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="completed", nullable=False)
    warnings: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class Bookmark(Base):
    __tablename__ = "bookmarks"
    __table_args__ = (UniqueConstraint("article_id", name="uq_bookmark_article"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("articles.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    article: Mapped[Article] = relationship(back_populates="bookmarks")


class AppSettings(Base):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Karachi", nullable=False)
    scheduled_time: Mapped[str] = mapped_column(String(5), default="09:00", nullable=False)
    schedule_enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    enabled_categories: Mapped[str] = mapped_column(Text, default='["ai","cybersecurity"]', nullable=False)
    articles_per_category: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    preferred_sources: Mapped[str] = mapped_column(Text, default="[]", nullable=False)


class AgentIdentity(Base):
    __tablename__ = "agent_identities"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_agent_identity_status"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    agent_name: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    allowed_tools: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    grants: Mapped[list["PermissionGrant"]] = relationship(back_populates="agent")


class PermissionGrant(Base):
    __tablename__ = "permission_grants"
    __table_args__ = (
        Index("ix_permission_grants_agent_status_expiry", "agent_id", "status", "expires_at"),
        CheckConstraint("status IN ('active', 'revoked')", name="ck_permission_grant_status"),
        CheckConstraint("expires_at > granted_at", name="ck_permission_grant_expiry"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent_identities.id"), nullable=False)
    granted_by: Mapped[str] = mapped_column(String(180), nullable=False)
    resource_scope: Mapped[str] = mapped_column(String(300), nullable=False)
    allowed_actions: Mapped[str] = mapped_column(Text, nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), default="active", nullable=False)
    approval_reference: Mapped[str | None] = mapped_column(String(80))
    agent: Mapped[AgentIdentity] = relationship(back_populates="grants")


class ActionRequest(Base):
    __tablename__ = "action_requests"
    __table_args__ = (
        Index("ix_action_requests_status_created", "status", "created_at"),
        CheckConstraint(
            "status IN ('Pending Approval', 'Approved', 'Rejected', 'Expired', 'Executing', 'Completed', 'Failed', 'Revoked')",
            name="ck_action_request_status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agent_identities.id"), nullable=False)
    requested_action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_resource: Mapped[str] = mapped_column(String(300), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    permission_id: Mapped[str | None] = mapped_column(ForeignKey("permission_grants.id"))
    requested_by: Mapped[str] = mapped_column(String(180), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    execution_result: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint("action_request_id", name="uq_approval_action_request"),
        Index("ix_approval_requests_decision_expiry", "decision", "expires_at"),
        CheckConstraint("decision IN ('pending', 'approved', 'rejected', 'expired')", name="ck_approval_decision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    action_request_id: Mapped[str] = mapped_column(ForeignKey("action_requests.id"), nullable=False)
    requested_by: Mapped[str] = mapped_column(String(180), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(180))
    decision: Mapped[str] = mapped_column(String(24), default="pending", nullable=False)
    decision_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_timestamp", "timestamp"),
        Index("ix_audit_logs_agent_timestamp", "agent_id", "timestamp"),
        Index("ix_audit_logs_correlation", "correlation_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agent_identities.id"))
    user_id: Mapped[str | None] = mapped_column(String(180))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    resource: Mapped[str] = mapped_column(String(300), nullable=False)
    authorization_result: Mapped[str] = mapped_column(String(24), nullable=False)
    permission_id: Mapped[str | None] = mapped_column(ForeignKey("permission_grants.id"))
    approval_reference: Mapped[str | None] = mapped_column(String(80))
    execution_result: Mapped[str | None] = mapped_column(Text)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
