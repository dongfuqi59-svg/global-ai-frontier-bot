from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class Category(str, Enum):
    FOUNDATION_MODEL = "foundation_model"
    AGENT = "agent"
    MULTIMODAL = "multimodal"
    OPEN_SOURCE = "open_source"
    INFRA_CHIP = "infra_chip"
    RESEARCH = "research"
    PRODUCT = "product"
    FUNDING = "funding"
    POLICY_SAFETY = "policy_safety"
    OTHER = "other"


class ProcessingStatus(str, Enum):
    PENDING = "PENDING"
    PROCESSED = "PROCESSED"
    REJECTED = "REJECTED"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"


class SourceConfig(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    name: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    type: str = Field(pattern=r"^(rss|atom|arxiv|github_release)$")
    category: Category
    language: str = Field(min_length=2, max_length=12)
    source_region: str = Field(default="foreign", pattern=r"^(domestic|foreign)$")
    credibility_weight: float = Field(ge=0, le=1)
    enabled: bool = True


class ArticleCandidate(BaseModel):
    source_id: str
    source_name: str
    source_type: str
    source_region: str = Field(default="foreign", pattern=r"^(domestic|foreign)$")
    source_credibility_weight: float = Field(ge=0, le=1)
    canonical_url: str
    title_original: str = Field(min_length=1, max_length=500)
    excerpt_original: str = Field(default="", max_length=6000)
    author: str | None = Field(default=None, max_length=300)
    language: str
    category: Category
    published_at: datetime | None
    published_raw: str | None = Field(default=None, max_length=300)
    source_timezone: str | None = Field(default=None, max_length=80)
    first_seen_at: datetime
    last_checked_at: datetime

    @field_validator("published_at", "first_seen_at", "last_checked_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return value


class AnalysisResult(BaseModel):
    title_zh: str = Field(min_length=1, max_length=300)
    summary_zh: list[str] = Field(min_length=1, max_length=3)
    why_it_matters: str = Field(min_length=1, max_length=500)
    category: Category
    entities: list[str] = Field(default_factory=list, max_length=20)
    frontier_relevance: int = Field(ge=0, le=100)
    industry_impact: int = Field(ge=0, le=100)
    novelty: int = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    is_ai_frontier: bool
    rejection_reason: str | None = Field(default=None, max_length=500)

    @field_validator("summary_zh")
    @classmethod
    def validate_summary_lines(cls, value: list[str]) -> list[str]:
        if any(not line.strip() or len(line) > 500 for line in value):
            raise ValueError("invalid summary line")
        return value


class FactCheckResult(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list, max_length=20)
    corrected_title_zh: str = Field(min_length=1, max_length=300)
    corrected_summary_zh: list[str] = Field(min_length=1, max_length=3)
    corrected_why_it_matters: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)


class EditorialSelection(BaseModel):
    selected_article_ids: list[str]
    selection_notes: list[str]
    coverage_categories: list[Category]
    insufficient_quality_candidates: bool


class Article(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    article_id: str
    canonical_url: str
    url_hash: str
    source_id: str
    source_name: str
    source_type: str
    source_region: str = Field(default="foreign", pattern=r"^(domestic|foreign)$")
    title_original: str
    title_zh: str
    language: str
    excerpt_original: str
    author: str | None = None
    summary_zh: list[str]
    why_it_matters: str
    category: Category
    entities: list[str] = Field(default_factory=list)
    published_at: datetime | None
    published_at_display: str
    published_raw: str | None = None
    source_timezone: str | None = None
    first_seen_at: datetime
    last_checked_at: datetime
    title_hash: str
    content_hash: str
    related_sources: list[dict[str, str]] = Field(default_factory=list)
    frontier_relevance: float = Field(ge=0, le=100)
    source_credibility: float = Field(ge=0, le=100)
    industry_impact: float = Field(ge=0, le=100)
    novelty: float = Field(ge=0, le=100)
    freshness: float = Field(ge=0, le=100)
    final_score: float = Field(ge=0, le=100)
    analysis_confidence: float = Field(ge=0, le=1)
    processing_status: ProcessingStatus
    expires_at: int
    published_partition: str = "ARTICLE"


class Digest(BaseModel):
    digest_date: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    window_start: datetime
    window_end: datetime
    article_ids: list[str]
    generated_at: datetime
    content_version: int = 1
    status: str = Field(pattern=r"^(PREPARED|PUBLISHED)$")
    message_payload: dict[str, Any]


class Delivery(BaseModel):
    delivery_id: str
    digest_date: str
    channel: str = "feishu"
    status: str = Field(pattern=r"^(PENDING|IN_PROGRESS|SUCCEEDED|FAILED)$")
    attempt_count: int = Field(ge=0)
    last_attempt_at: datetime | None = None
    message_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
