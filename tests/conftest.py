from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.models import Article, Category, ProcessingStatus

UTC = timezone.utc


@pytest.fixture
def article_factory():
    def build(
        article_id: str,
        *,
        category: Category = Category.RESEARCH,
        score: float = 70,
        entity: str = "Example AI",
        published_at: datetime | None = None,
        source_region: str = "foreign",
        status: ProcessingStatus = ProcessingStatus.PROCESSED,
    ) -> Article:
        published = published_at or datetime(2026, 7, 31, 1, 0, tzinfo=UTC)
        return Article(
            article_id=article_id,
            canonical_url=f"https://example.com/{article_id}",
            url_hash=f"url-{article_id}",
            source_id="source",
            source_name="Official Source",
            source_type="rss",
            source_region=source_region,
            title_original=f"AI release {article_id}",
            title_zh=f"AI 发布 {article_id}",
            language="en",
            excerpt_original="A material AI update.",
            summary_zh=["发布了一项 AI 更新。", "该更新包含明确的技术变化。"],
            why_it_matters="这会影响开发者采用相关技术。",
            category=category,
            entities=[entity],
            published_at=published,
            published_at_display="2026-07-31 09:00",
            first_seen_at=published,
            last_checked_at=published,
            title_hash=f"title-{article_id}",
            content_hash=f"content-{article_id}",
            related_sources=[],
            frontier_relevance=score,
            source_credibility=90,
            industry_impact=score,
            novelty=score,
            freshness=80,
            final_score=score,
            analysis_confidence=0.9,
            processing_status=status,
            expires_at=1_800_000_000,
        )

    return build
