from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from src.collectors.manager import CollectionBatch, SourceCollector
from src.models import Article, ProcessingStatus, SourceConfig
from src.repositories.base import Repository
from src.services.analysis import AnalysisService, build_article
from src.services.dedup import find_duplicate, related_source
from src.utils.logging import log_event
from src.utils.text import stable_hash
from src.utils.time import ensure_utc, publication_is_valid

logger = logging.getLogger(__name__)


@dataclass
class CollectionResult:
    successful_sources: int
    failed_sources: int
    fetched: int
    added: int
    duplicates: int
    rejected: int
    analysis_failures: int


class CollectionService:
    def __init__(
        self,
        repository: Repository,
        collector: SourceCollector,
        analyzer: AnalysisService,
        retention_days: int,
    ) -> None:
        self._repository = repository
        self._collector = collector
        self._analyzer = analyzer
        self._retention_days = retention_days

    async def run(
        self, sources: list[SourceConfig], now_utc: datetime
    ) -> CollectionResult:
        now = ensure_utc(now_utc)
        batch = await self._collector.collect(sources, now)
        recent = await self._repository.query_articles(
            now - timedelta(days=7), now + timedelta(minutes=5)
        )
        return await self.process_batch(batch, recent, now)

    async def process_batch(
        self,
        batch: CollectionBatch,
        recent: list[Article],
        now_utc: datetime,
    ) -> CollectionResult:
        added = duplicates = rejected = analysis_failures = 0
        candidates = sorted(
            batch.candidates,
            key=lambda item: item.source_credibility_weight,
            reverse=True,
        )
        for candidate in candidates:
            article_id = stable_hash(candidate.canonical_url)[:32]
            exact = await self._repository.get_article(article_id)
            if exact is not None:
                if not str(exact.processing_status).endswith("ANALYSIS_FAILED"):
                    await self._repository.touch_article(article_id, now_utc)
                    duplicates += 1
                    continue
                retry_candidate = candidate.model_copy(
                    update={"first_seen_at": exact.first_seen_at}
                )
                retry_outcome = await self._analyzer.analyze(retry_candidate)
                if retry_outcome.llm_deferred:
                    await self._repository.touch_article(article_id, now_utc)
                    analysis_failures += 1
                    duplicates += 1
                    continue
                retried = build_article(
                    retry_candidate,
                    retry_outcome,
                    now_utc,
                    self._retention_days,
                )
                retried.related_sources = exact.related_sources
                await self._repository.put_article(retried)
                recent[:] = [
                    item for item in recent if item.article_id != exact.article_id
                ]
                recent.append(retried)
                if retry_outcome.llm_failed:
                    analysis_failures += 1
                duplicates += 1
                continue

            match = find_duplicate(candidate, recent)
            outcome = await self._analyzer.analyze(candidate)
            article = build_article(
                candidate, outcome, now_utc, self._retention_days
            )
            if not publication_is_valid(candidate.published_at, now_utc):
                article.processing_status = ProcessingStatus.REJECTED
                rejected += 1
                if candidate.published_at and candidate.published_at > now_utc:
                    log_event(
                        logger,
                        logging.WARNING,
                        "abnormal_future_publication_time",
                        source_id=candidate.source_id,
                        published_at=candidate.published_at,
                    )
            elif outcome.llm_failed:
                analysis_failures += 1

            if candidate.published_at:
                delay = now_utc - ensure_utc(candidate.published_at)
                if delay > timedelta(hours=1):
                    log_event(
                        logger,
                        logging.INFO,
                        "delayed_source_update",
                        source_id=candidate.source_id,
                        delay_seconds=int(delay.total_seconds()),
                    )

            if match is not None:
                if article.source_credibility > match.article.source_credibility + 5:
                    article.related_sources.append(
                        {
                            "source_id": match.article.source_id,
                            "source_name": match.article.source_name,
                            "url": match.article.canonical_url,
                        }
                    )
                    await self._repository.mark_duplicate(match.article.article_id)
                    await self._repository.put_article(article)
                    recent[:] = [
                        item
                        for item in recent
                        if item.article_id != match.article.article_id
                    ]
                    recent.append(article)
                    added += 1
                else:
                    await self._repository.add_related_source(
                        match.article.article_id, related_source(candidate)
                    )
                    await self._repository.touch_article(
                        match.article.article_id, now_utc
                    )
                    duplicates += 1
                continue

            await self._repository.put_article(article)
            recent.append(article)
            added += 1

        result = CollectionResult(
            successful_sources=batch.successful_sources,
            failed_sources=batch.failed_sources,
            fetched=len(batch.candidates),
            added=added,
            duplicates=duplicates,
            rejected=rejected,
            analysis_failures=analysis_failures,
        )
        log_event(logger, logging.INFO, "collection_completed", **result.__dict__)
        return result
