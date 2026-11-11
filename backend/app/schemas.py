from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    description: str
    summary: str
    category: str
    source_name: str
    source_url: str
    published_at: datetime
    collected_at: datetime
    importance_score: float
    score_reasons: str
    verification_status: str
    impact_analysis: str
    severity: str | None
    cve_ids: str
    image_url: str | None
    image_alt: str | None
    image_credit: str | None
    image_source_name: str | None
    image_source_url: str | None
    image_license: str | None
    image_type: str
    is_illustrative: bool
    image_verified: bool
    image_checked_at: datetime | None


class ReportOut(BaseModel):
    id: int
    report_date: str
    executive_summary: str
    ai_news: list[int]
    cybersecurity_news: list[int]
    critical_alerts: list[int]
    key_takeaways: list[str]
    impact_analysis: str
    sources: list[str]
    status: str
    warnings: list[str]
    created_at: datetime


class SettingsUpdate(BaseModel):
    timezone: str = Field(min_length=1, max_length=80)
    scheduled_time: str = Field(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    schedule_enabled: bool = True
    enabled_categories: list[Literal["ai", "cybersecurity"]] = Field(min_length=1)
    articles_per_category: int = Field(ge=1, le=20)
    preferred_sources: list[str] = Field(default_factory=list, max_length=40)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Unknown IANA timezone") from error
        return value

    @field_validator("preferred_sources")
    @classmethod
    def clean_sources(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(source.strip() for source in values if source.strip()))


class BookmarkCreate(BaseModel):
    article_id: int = Field(gt=0)


class PermissionGrantCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=80)
    resource_scope: str = Field(min_length=1, max_length=300)
    allowed_actions: list[str] = Field(min_length=1, max_length=20)
    expires_in_minutes: int = Field(ge=1, le=43200)
    approval_reference: str | None = Field(default=None, max_length=80)


class PermissionRevoke(BaseModel):
    permission_id: str = Field(min_length=1, max_length=36)
    reason: str = Field(min_length=1, max_length=1000)


class ActionRequestCreate(BaseModel):
    agent_id: str = Field(min_length=1, max_length=80)
    requested_action: str = Field(min_length=1, max_length=120)
    target_resource: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=2000)


class ApprovalDecision(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)
