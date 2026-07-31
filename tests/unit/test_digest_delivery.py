from datetime import datetime, timezone

import pytest

from src.models import Digest
from src.repositories.base import InMemoryRepository
from src.services.digest import DigestService
from src.services.feishu import FeishuPublishError

UTC = timezone.utc


def _digest() -> Digest:
    return Digest(
        digest_date="2026-07-31",
        window_start=datetime(2026, 7, 30, 1, 50, tzinfo=UTC),
        window_end=datetime(2026, 7, 31, 1, 50, tzinfo=UTC),
        article_ids=[],
        generated_at=datetime(2026, 7, 31, 1, 55, tzinfo=UTC),
        status="PREPARED",
        message_payload={"msg_type": "interactive", "card": {}},
    )


@pytest.mark.asyncio
async def test_publish_is_idempotent_and_failed_delivery_can_retry() -> None:
    repository = InMemoryRepository()
    await repository.put_digest(_digest())
    service = DigestService(repository, 10)

    class Sender:
        calls = 0

        async def send(self, _payload):
            self.calls += 1
            if self.calls == 1:
                raise FeishuPublishError("TEMPORARY", "temporary failure")
            return "message-1"

    sender = Sender()
    now = datetime(2026, 7, 31, 2, 0, tzinfo=UTC)
    with pytest.raises(FeishuPublishError):
        await service.publish(now, sender)
    delivery_id = "2026-07-31#feishu#group_default"
    failed = await repository.get_delivery(delivery_id)
    assert failed is not None
    assert failed.status == "FAILED"
    assert failed.attempt_count == 1

    assert (
        await service.publish(now, sender, retry_only=True)
        == "PUBLISHED"
    )
    succeeded = await repository.get_delivery(delivery_id)
    assert succeeded is not None
    assert succeeded.status == "SUCCEEDED"
    assert succeeded.attempt_count == 2
    assert (
        await service.publish(now, sender, retry_only=True)
        == "SKIPPED_ALREADY_SUCCEEDED"
    )
    assert sender.calls == 2


@pytest.mark.asyncio
async def test_conditional_retry_does_not_publish_without_failed_status() -> None:
    repository = InMemoryRepository()
    await repository.put_digest(_digest())
    service = DigestService(repository, 10)

    class Sender:
        calls = 0

        async def send(self, _payload):
            self.calls += 1
            return None

    sender = Sender()
    status = await service.publish(
        datetime(2026, 7, 31, 2, 2, tzinfo=UTC),
        sender,
        retry_only=True,
    )
    assert status == "SKIPPED_NOT_FAILED"
    assert sender.calls == 0


@pytest.mark.asyncio
async def test_conditional_retry_recovers_stale_in_progress_delivery() -> None:
    repository = InMemoryRepository()
    await repository.put_digest(_digest())
    delivery_id = "2026-07-31#feishu#group_default"
    await repository.claim_delivery(
        delivery_id,
        "2026-07-31",
        datetime(2026, 7, 31, 2, 0, tzinfo=UTC),
    )

    class Sender:
        async def send(self, _payload):
            return "recovered-message"

    status = await DigestService(repository, 10).publish(
        datetime(2026, 7, 31, 2, 2, tzinfo=UTC),
        Sender(),
        retry_only=True,
    )
    assert status == "PUBLISHED"
    delivery = await repository.get_delivery(delivery_id)
    assert delivery is not None
    assert delivery.status == "SUCCEEDED"
    assert delivery.attempt_count == 2


@pytest.mark.asyncio
async def test_empty_digest_is_prepared_explicitly() -> None:
    repository = InMemoryRepository()
    digest = await DigestService(repository, 10).prepare(
        datetime(2026, 7, 31, 1, 55, tzinfo=UTC)
    )
    assert digest.article_ids == []
    elements = digest.message_payload["card"]["elements"]
    assert any("今日暂无" in str(element) for element in elements)


@pytest.mark.asyncio
async def test_digest_selects_domestic_and_foreign_groups(article_factory) -> None:
    repository = InMemoryRepository()
    for index in range(12):
        await repository.put_article(
            article_factory(
                f"domestic-{index}",
                source_region="domestic",
                score=90 - index,
            )
        )
        await repository.put_article(
            article_factory(
                f"foreign-{index}",
                source_region="foreign",
                score=88 - index,
            )
        )

    digest = await DigestService(repository, 10).prepare(
        datetime(2026, 7, 31, 1, 55, tzinfo=UTC)
    )

    assert len(digest.article_ids) == 20
    domestic_count = sum(
        article_id.startswith("domestic-") for article_id in digest.article_ids
    )
    foreign_count = sum(
        article_id.startswith("foreign-") for article_id in digest.article_ids
    )
    assert domestic_count == 10
    assert foreign_count == 10
