from datetime import datetime, timezone

import pytest

from src.models import Digest
from src.repositories.file import JsonFileRepository

UTC = timezone.utc


@pytest.mark.asyncio
async def test_json_file_repository_persists_articles_and_digests(
    tmp_path, article_factory
) -> None:
    state_file = tmp_path / "state" / "bot-state.json"
    repository = JsonFileRepository(state_file)
    article = article_factory(
        "article-1", published_at=datetime(2026, 7, 31, 1, 0, tzinfo=UTC)
    )
    await repository.put_article(article)
    await repository.put_digest(
        Digest(
            digest_date="2026-07-31",
            window_start=datetime(2026, 7, 30, 1, 50, tzinfo=UTC),
            window_end=datetime(2026, 7, 31, 1, 50, tzinfo=UTC),
            article_ids=[article.article_id],
            generated_at=datetime(2026, 7, 31, 1, 55, tzinfo=UTC),
            status="PREPARED",
            message_payload={"msg_type": "interactive", "card": {}},
        )
    )

    reloaded = JsonFileRepository(state_file)
    articles = await reloaded.query_articles(
        datetime(2026, 7, 30, 1, 50, tzinfo=UTC),
        datetime(2026, 7, 31, 1, 50, tzinfo=UTC),
    )
    digest = await reloaded.get_digest("2026-07-31")

    assert [item.article_id for item in articles] == ["article-1"]
    assert digest is not None
    assert digest.article_ids == ["article-1"]


@pytest.mark.asyncio
async def test_json_file_repository_delivery_claims_survive_reload(tmp_path) -> None:
    state_file = tmp_path / "bot-state.json"
    repository = JsonFileRepository(state_file)
    delivery = await repository.claim_delivery(
        "2026-07-31#feishu#group_default",
        "2026-07-31",
        datetime(2026, 7, 31, 2, 0, tzinfo=UTC),
    )
    assert delivery is not None
    assert delivery.attempt_count == 1
    await repository.complete_delivery(
        delivery.delivery_id, succeeded=False, error_code="NETWORK_ERROR"
    )

    reloaded = JsonFileRepository(state_file)
    retry = await reloaded.claim_delivery(
        "2026-07-31#feishu#group_default",
        "2026-07-31",
        datetime(2026, 7, 31, 2, 2, tzinfo=UTC),
    )

    assert retry is not None
    assert retry.attempt_count == 2
