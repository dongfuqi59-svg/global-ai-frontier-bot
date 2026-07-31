from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Protocol

from src.models import Article, Digest, EditorialSelection
from src.prompts.system import EDITOR_SYSTEM_PROMPT
from src.repositories.base import Repository
from src.services.feishu import FeishuPublishError, build_digest_payload
from src.services.llm import LLMClient
from src.services.scoring import select_diverse_articles
from src.utils.logging import log_event, redact
from src.utils.time import digest_window, ensure_utc, publication_is_valid

logger = logging.getLogger(__name__)
LOW_QUALITY_TERMS = {
    "sponsored",
    "advertorial",
    "top 10 tools",
    "best ai deals",
    "限时优惠",
    "促销",
}


class MessageSender(Protocol):
    async def send(self, message_payload: dict[str, Any]) -> str | None: ...


class DigestService:
    def __init__(
        self,
        repository: Repository,
        item_limit: int,
        *,
        llm_client: LLMClient | None = None,
    ) -> None:
        self._repository = repository
        self._item_limit = item_limit
        self._llm = llm_client

    async def prepare(self, now_utc: datetime) -> Digest:
        digest_date, window_start, window_end = digest_window(now_utc)
        existing = await self._repository.get_digest(digest_date)
        if existing and existing.status == "PUBLISHED":
            return existing
        articles = await self._repository.query_articles(window_start, window_end)
        eligible = [
            article
            for article in articles
            if publication_is_valid(
                article.published_at,
                ensure_utc(now_utc),
                window_start=window_start,
                window_end=window_end,
            )
            and str(article.processing_status)
            in {
                "PROCESSED",
                "ANALYSIS_FAILED",
                "ProcessingStatus.PROCESSED",
                "ProcessingStatus.ANALYSIS_FAILED",
            }
            and article.final_score >= 45
            and not _looks_low_quality(
                f"{article.title_original} {article.excerpt_original}"
            )
        ]
        preferred = await self._editor_preferences(eligible, window_start, window_end)
        selected_domestic = select_diverse_articles(
            [
                article
                for article in eligible
                if article.source_region == "domestic"
            ],
            self._item_limit,
            max_per_topic=self._item_limit,
            preferred_article_ids=preferred,
        )
        selected_foreign = select_diverse_articles(
            [
                article
                for article in eligible
                if article.source_region == "foreign"
            ],
            self._item_limit,
            max_per_topic=self._item_limit,
            preferred_article_ids=preferred,
        )
        selected = [*selected_domestic, *selected_foreign]
        payload = build_digest_payload(
            digest_date, window_start, window_end, selected
        )
        digest = Digest(
            digest_date=digest_date,
            window_start=window_start,
            window_end=window_end,
            article_ids=[article.article_id for article in selected],
            generated_at=ensure_utc(now_utc),
            content_version=(existing.content_version + 1 if existing else 1),
            status="PREPARED",
            message_payload=payload,
        )
        await self._repository.put_digest(digest)
        log_event(
            logger,
            logging.INFO,
            "digest_prepared",
            digest_date=digest_date,
            candidate_count=len(eligible),
            selected_count=len(selected),
            domestic_count=len(selected_domestic),
            foreign_count=len(selected_foreign),
        )
        return digest

    async def _editor_preferences(
        self,
        articles: list[Article],
        window_start: datetime,
        window_end: datetime,
    ) -> set[str]:
        if self._llm is None or not articles:
            return set()
        candidates = [
            {
                "article_id": article.article_id,
                "title_zh": article.title_zh,
                "summary_zh": article.summary_zh,
                "category": article.category,
                "source_name": article.source_name,
                "final_score": article.final_score,
                "published_at": article.published_at,
            }
            for article in sorted(
                articles, key=lambda item: item.final_score, reverse=True
            )[:40]
        ]
        try:
            selection = await self._llm.complete_json(
                EDITOR_SYSTEM_PROMPT,
                {
                    "window_start": window_start,
                    "window_end": window_end,
                    "item_limit": self._item_limit,
                    "candidates": candidates,
                },
                EditorialSelection,
            )
            valid_ids = {article.article_id for article in articles}
            return set(selection.selected_article_ids) & valid_ids
        except Exception:
            log_event(logger, logging.WARNING, "digest_editor_fallback")
            return set()

    async def publish(
        self,
        now_utc: datetime,
        sender: MessageSender,
        *,
        retry_only: bool = False,
    ) -> str:
        digest_date, _, _ = digest_window(now_utc)
        digest = await self._repository.get_digest(digest_date)
        if digest is None:
            raise RuntimeError(f"digest {digest_date} has not been prepared")
        delivery_id = f"{digest_date}#feishu#group_default"
        current = await self._repository.get_delivery(delivery_id)
        if current and current.status == "SUCCEEDED":
            return "SKIPPED_ALREADY_SUCCEEDED"
        if retry_only:
            stale_in_progress = (
                current is not None
                and current.status == "IN_PROGRESS"
                and current.last_attempt_at is not None
                and ensure_utc(current.last_attempt_at)
                <= ensure_utc(now_utc) - timedelta(seconds=60)
            )
            if current is None or (
                current.status != "FAILED" and not stale_in_progress
            ):
                return "SKIPPED_NOT_FAILED"
        claim = await self._repository.claim_delivery(
            delivery_id, digest_date, ensure_utc(now_utc)
        )
        if claim is None:
            return "SKIPPED_NOT_CLAIMED"
        try:
            message_id = await sender.send(digest.message_payload)
        except FeishuPublishError as exc:
            await self._repository.complete_delivery(
                delivery_id,
                succeeded=False,
                error_code=exc.code,
                error_message=str(redact(str(exc))),
            )
            log_event(
                logger,
                logging.ERROR,
                "digest_publish_failed",
                digest_date=digest_date,
                attempt_count=claim.attempt_count,
                error_code=exc.code,
            )
            raise
        except Exception as exc:
            await self._repository.complete_delivery(
                delivery_id,
                succeeded=False,
                error_code=type(exc).__name__,
                error_message=str(redact(str(exc))),
            )
            raise
        await self._repository.complete_delivery(
            delivery_id, succeeded=True, message_id=message_id
        )
        await self._repository.mark_digest_published(digest_date)
        log_event(
            logger,
            logging.INFO,
            "digest_publish_succeeded",
            digest_date=digest_date,
            attempt_count=claim.attempt_count,
        )
        return "PUBLISHED"


def _looks_low_quality(value: str) -> bool:
    lowered = value.casefold()
    return any(term in lowered for term in LOW_QUALITY_TERMS)
