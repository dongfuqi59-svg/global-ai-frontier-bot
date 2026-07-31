from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel

from src.models import Article, Delivery, Digest, ProcessingStatus
from src.utils.time import ensure_utc

ModelT = TypeVar("ModelT", bound=BaseModel)


class JsonFileRepository:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._articles: dict[str, Article] = {}
        self._digests: dict[str, Digest] = {}
        self._deliveries: dict[str, Delivery] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        with self._path.open(encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            raise ValueError(f"invalid state file: {self._path}")
        self._articles = _load_models(payload.get("articles", {}), Article)
        self._digests = _load_models(payload.get("digests", {}), Digest)
        self._deliveries = _load_models(payload.get("deliveries", {}), Delivery)

    def _save(self) -> None:
        payload = {
            "articles": _dump_models(self._articles),
            "digests": _dump_models(self._digests),
            "deliveries": _dump_models(self._deliveries),
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self._path.with_name(f".{self._path.name}.tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            file.write("\n")
        os.replace(temp_path, self._path)

    async def get_article(self, article_id: str) -> Article | None:
        article = self._articles.get(article_id)
        return article.model_copy(deep=True) if article else None

    async def put_article(self, article: Article) -> None:
        self._articles[article.article_id] = article.model_copy(deep=True)
        self._save()

    async def touch_article(self, article_id: str, checked_at: datetime) -> None:
        self._articles[article_id].last_checked_at = checked_at
        self._save()

    async def query_articles(
        self, window_start: datetime, window_end: datetime
    ) -> list[Article]:
        start = ensure_utc(window_start)
        end = ensure_utc(window_end)
        return [
            article.model_copy(deep=True)
            for article in self._articles.values()
            if article.published_at is not None
            and start < ensure_utc(article.published_at) <= end
        ]

    async def add_related_source(
        self, article_id: str, related: dict[str, str]
    ) -> None:
        article = self._articles[article_id]
        if related not in article.related_sources:
            article.related_sources.append(related)
            self._save()

    async def mark_duplicate(self, article_id: str) -> None:
        article = self._articles[article_id]
        article.processing_status = ProcessingStatus.REJECTED
        self._save()

    async def get_digest(self, digest_date: str) -> Digest | None:
        digest = self._digests.get(digest_date)
        return digest.model_copy(deep=True) if digest else None

    async def put_digest(self, digest: Digest) -> None:
        self._digests[digest.digest_date] = digest.model_copy(deep=True)
        self._save()

    async def mark_digest_published(self, digest_date: str) -> None:
        digest = self._digests[digest_date]
        digest.status = "PUBLISHED"
        self._save()

    async def get_delivery(self, delivery_id: str) -> Delivery | None:
        delivery = self._deliveries.get(delivery_id)
        return delivery.model_copy(deep=True) if delivery else None

    async def claim_delivery(
        self, delivery_id: str, digest_date: str, now_utc: datetime
    ) -> Delivery | None:
        current = self._deliveries.get(delivery_id)
        now = ensure_utc(now_utc)
        if current and current.status == "SUCCEEDED":
            return None
        if (
            current
            and current.status == "IN_PROGRESS"
            and current.last_attempt_at
            and ensure_utc(current.last_attempt_at) > now - timedelta(seconds=60)
        ):
            return None
        attempt_count = (current.attempt_count if current else 0) + 1
        delivery = Delivery(
            delivery_id=delivery_id,
            digest_date=digest_date,
            status="IN_PROGRESS",
            attempt_count=attempt_count,
            last_attempt_at=now,
        )
        self._deliveries[delivery_id] = delivery
        self._save()
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
        delivery = self._deliveries[delivery_id]
        delivery.status = "SUCCEEDED" if succeeded else "FAILED"
        delivery.message_id = message_id
        delivery.error_code = error_code
        delivery.error_message = error_message
        self._save()


def _load_models(payload: Any, model_type: type[ModelT]) -> dict[str, ModelT]:
    if not isinstance(payload, dict):
        raise ValueError("state section must be a JSON object")
    return {
        str(key): model_type.model_validate(value)
        for key, value in payload.items()
    }


def _dump_models(items: dict[str, ModelT]) -> dict[str, Any]:
    return {
        key: value.model_dump(mode="json", exclude_none=True)
        for key, value in sorted(items.items())
    }
