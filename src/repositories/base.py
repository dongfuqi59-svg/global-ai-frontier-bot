from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from src.models import Article, Delivery, Digest, ProcessingStatus
from src.utils.time import ensure_utc


class Repository(Protocol):
    async def get_article(self, article_id: str) -> Article | None: ...

    async def put_article(self, article: Article) -> None: ...

    async def touch_article(self, article_id: str, checked_at: datetime) -> None: ...

    async def query_articles(
        self, window_start: datetime, window_end: datetime
    ) -> list[Article]: ...

    async def add_related_source(
        self, article_id: str, related: dict[str, str]
    ) -> None: ...

    async def mark_duplicate(self, article_id: str) -> None: ...

    async def get_digest(self, digest_date: str) -> Digest | None: ...

    async def put_digest(self, digest: Digest) -> None: ...

    async def mark_digest_published(self, digest_date: str) -> None: ...

    async def get_delivery(self, delivery_id: str) -> Delivery | None: ...

    async def claim_delivery(
        self, delivery_id: str, digest_date: str, now_utc: datetime
    ) -> Delivery | None: ...

    async def complete_delivery(
        self,
        delivery_id: str,
        *,
        succeeded: bool,
        message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None: ...


class InMemoryRepository:
    def __init__(self) -> None:
        self.articles: dict[str, Article] = {}
        self.digests: dict[str, Digest] = {}
        self.deliveries: dict[str, Delivery] = {}

    async def get_article(self, article_id: str) -> Article | None:
        return self.articles.get(article_id)

    async def put_article(self, article: Article) -> None:
        self.articles[article.article_id] = article.model_copy(deep=True)

    async def touch_article(self, article_id: str, checked_at: datetime) -> None:
        self.articles[article_id].last_checked_at = checked_at

    async def query_articles(
        self, window_start: datetime, window_end: datetime
    ) -> list[Article]:
        start = ensure_utc(window_start)
        end = ensure_utc(window_end)
        return [
            article.model_copy(deep=True)
            for article in self.articles.values()
            if article.published_at is not None
            and start < ensure_utc(article.published_at) <= end
        ]

    async def add_related_source(
        self, article_id: str, related: dict[str, str]
    ) -> None:
        article = self.articles[article_id]
        if related not in article.related_sources:
            article.related_sources.append(related)

    async def mark_duplicate(self, article_id: str) -> None:
        article = self.articles[article_id]
        article.processing_status = ProcessingStatus.REJECTED

    async def get_digest(self, digest_date: str) -> Digest | None:
        digest = self.digests.get(digest_date)
        return digest.model_copy(deep=True) if digest else None

    async def put_digest(self, digest: Digest) -> None:
        self.digests[digest.digest_date] = digest.model_copy(deep=True)

    async def mark_digest_published(self, digest_date: str) -> None:
        digest = self.digests[digest_date]
        digest.status = "PUBLISHED"

    async def get_delivery(self, delivery_id: str) -> Delivery | None:
        delivery = self.deliveries.get(delivery_id)
        return delivery.model_copy(deep=True) if delivery else None

    async def claim_delivery(
        self, delivery_id: str, digest_date: str, now_utc: datetime
    ) -> Delivery | None:
        current = self.deliveries.get(delivery_id)
        if current and current.status == "SUCCEEDED":
            return None
        if (
            current
            and current.status == "IN_PROGRESS"
            and current.last_attempt_at
            and ensure_utc(current.last_attempt_at)
            > ensure_utc(now_utc) - timedelta(seconds=60)
        ):
            return None
        attempt_count = (current.attempt_count if current else 0) + 1
        delivery = Delivery(
            delivery_id=delivery_id,
            digest_date=digest_date,
            status="IN_PROGRESS",
            attempt_count=attempt_count,
            last_attempt_at=now_utc,
        )
        self.deliveries[delivery_id] = delivery
        return delivery.model_copy(deep=True)

    async def complete_delivery(
        self,
        delivery_id: str,
        *,
        succeeded: bool,
        message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        delivery = self.deliveries[delivery_id]
        delivery.status = "SUCCEEDED" if succeeded else "FAILED"
        delivery.message_id = message_id
        delivery.error_code = error_code
        delivery.error_message = error_message
