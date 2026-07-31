from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.collectors.manager import CollectionBatch, SourceCollector
from src.models import AnalysisResult, ArticleCandidate, Category, SourceConfig
from src.repositories.base import InMemoryRepository
from src.services.analysis import (
    AnalysisOutcome,
    AnalysisService,
    deterministic_fallback,
)
from src.services.collection import CollectionService
from src.utils.text import stable_hash

UTC = timezone.utc


def _candidate() -> ArticleCandidate:
    now = datetime(2026, 7, 31, 1, tzinfo=UTC)
    return ArticleCandidate(
        source_id="official",
        source_name="Official AI Lab",
        source_type="rss",
        source_credibility_weight=1,
        canonical_url="https://example.com/release",
        title_original="AI Lab releases a new multimodal model",
        excerpt_original="The model processes text and images.",
        language="en",
        category=Category.MULTIMODAL,
        published_at=now,
        first_seen_at=now,
        last_checked_at=now,
    )


def test_model_output_schema_rejects_out_of_range_scores() -> None:
    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(
            {
                "title_zh": "标题",
                "summary_zh": ["摘要"],
                "why_it_matters": "重要性",
                "category": "research",
                "entities": [],
                "frontier_relevance": 101,
                "industry_impact": 50,
                "novelty": 50,
                "confidence": 0.8,
                "is_ai_frontier": True,
                "rejection_reason": None,
            }
        )


@pytest.mark.asyncio
async def test_llm_failure_uses_deterministic_fallback() -> None:
    class FailingLlm:
        calls = 0

        async def complete_json(self, *_args, **_kwargs):
            self.calls += 1
            raise TimeoutError("unavailable")

    llm = FailingLlm()
    service = AnalysisService(llm)
    outcome = await service.analyze(_candidate())
    assert outcome.llm_failed
    assert "发布新的 AI 资讯" in outcome.result.title_zh
    assert outcome.result.is_ai_frontier
    deferred = await service.analyze(_candidate())
    assert deferred.llm_deferred
    assert llm.calls == 1


def test_unrelated_policy_item_is_rejected_by_fallback() -> None:
    candidate = _candidate().model_copy(
        update={
            "category": Category.POLICY_SAFETY,
            "title_original": "Agency publishes routine meeting notice",
            "excerpt_original": "The meeting will discuss general administrative topics.",
        }
    )
    assert not deterministic_fallback(candidate).is_ai_frontier


def test_deterministic_fallback_truncates_long_summary_lines() -> None:
    candidate = _candidate().model_copy(
        update={"excerpt_original": "AI " + ("long text " * 120)}
    )
    result = deterministic_fallback(candidate)
    assert all(len(line) <= 500 for line in result.summary_zh)


@pytest.mark.asyncio
async def test_failed_analysis_is_retried_on_later_collection() -> None:
    class ToggleAnalyzer:
        failed = True

        async def analyze(self, candidate):
            return AnalysisOutcome(
                deterministic_fallback(candidate),
                llm_failed=self.failed,
            )

    class UnusedCollector:
        pass

    repository = InMemoryRepository()
    analyzer = ToggleAnalyzer()
    service = CollectionService(
        repository,
        UnusedCollector(),  # type: ignore[arg-type]
        analyzer,  # type: ignore[arg-type]
        180,
    )
    candidate = _candidate()
    batch = CollectionBatch(candidates=[candidate], successful_sources=1)
    await service.process_batch(batch, [], candidate.first_seen_at)
    article_id = stable_hash(candidate.canonical_url)[:32]
    failed = await repository.get_article(article_id)
    assert failed is not None
    assert str(failed.processing_status) == "ANALYSIS_FAILED"

    analyzer.failed = False
    await service.process_batch(batch, [failed], candidate.first_seen_at)
    retried = await repository.get_article(article_id)
    assert retried is not None
    assert str(retried.processing_status) == "PROCESSED"


@pytest.mark.asyncio
async def test_one_source_failure_does_not_block_other_sources() -> None:
    sources = [
        SourceConfig(
            id="good",
            name="Good",
            url="https://8.8.8.8/good.xml",
            type="rss",
            category=Category.RESEARCH,
            language="en",
            credibility_weight=1,
        ),
        SourceConfig(
            id="bad",
            name="Bad",
            url="https://8.8.8.8/bad.xml",
            type="rss",
            category=Category.RESEARCH,
            language="en",
            credibility_weight=1,
        ),
    ]
    feed = """<?xml version="1.0"?>
    <rss version="2.0"><channel><title>Good</title>
      <item><title>New AI research</title>
      <link>https://8.8.8.8/article</link>
      <description>A new AI method.</description>
      <pubDate>Thu, 31 Jul 2026 01:00:00 GMT</pubDate></item>
    </channel></rss>"""

    async def fetch(url: str) -> str:
        if "bad.xml" in url:
            raise OSError("source down")
        return feed

    result = await SourceCollector(2, 1).collect(
        sources, datetime(2026, 7, 31, 1, 10, tzinfo=UTC), fetch_text=fetch
    )
    assert result.successful_sources == 1
    assert result.failed_sources == 1
    assert len(result.candidates) == 1
