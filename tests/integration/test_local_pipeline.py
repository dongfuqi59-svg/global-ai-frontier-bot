from datetime import datetime, timezone

import pytest

from src.collectors.manager import CollectionBatch
from src.models import ArticleCandidate, Category
from src.repositories.base import InMemoryRepository
from src.services.analysis import AnalysisService
from src.services.collection import CollectionService
from src.services.digest import DigestService

UTC = timezone.utc


@pytest.mark.integration
@pytest.mark.asyncio
async def test_collection_prepare_and_publish_pipeline() -> None:
    repository = InMemoryRepository()
    now = datetime(2026, 7, 31, 1, 10, tzinfo=UTC)
    candidate = ArticleCandidate(
        source_id="official",
        source_name="Official AI Lab",
        source_type="rss",
        source_credibility_weight=1,
        canonical_url="https://example.com/new-model",
        title_original="AI Lab releases New Model",
        excerpt_original="The AI model adds a documented technical capability.",
        language="en",
        category=Category.FOUNDATION_MODEL,
        published_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC),
        first_seen_at=now,
        last_checked_at=now,
    )

    class UnusedCollector:
        pass

    collection = CollectionService(
        repository,
        UnusedCollector(),  # type: ignore[arg-type]
        AnalysisService(None),
        180,
    )
    result = await collection.process_batch(
        CollectionBatch(candidates=[candidate], successful_sources=1),
        [],
        now,
    )
    assert result.added == 1

    digest_service = DigestService(repository, 10)
    digest = await digest_service.prepare(
        datetime(2026, 7, 31, 1, 55, tzinfo=UTC)
    )
    assert len(digest.article_ids) == 1

    class Sender:
        async def send(self, _payload):
            return "message-id"

    status = await digest_service.publish(
        datetime(2026, 7, 31, 2, 0, tzinfo=UTC), Sender()
    )
    assert status == "PUBLISHED"
