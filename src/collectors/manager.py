from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from src.collectors.feed import parse_feed
from src.models import ArticleCandidate, SourceConfig
from src.utils.http import SafeHttpClient
from src.utils.logging import log_event

logger = logging.getLogger(__name__)


@dataclass
class CollectionBatch:
    candidates: list[ArticleCandidate] = field(default_factory=list)
    successful_sources: int = 0
    failed_sources: int = 0
    errors: dict[str, str] = field(default_factory=dict)


class SourceCollector:
    def __init__(self, concurrency: int, timeout_seconds: float) -> None:
        self._concurrency = concurrency
        self._timeout_seconds = timeout_seconds

    async def collect(
        self,
        sources: list[SourceConfig],
        checked_at: datetime,
        fetch_text: Callable[[str], Awaitable[str]] | None = None,
    ) -> CollectionBatch:
        if fetch_text is not None:
            return await self._collect_with_fetcher(sources, checked_at, fetch_text)
        async with SafeHttpClient(self._timeout_seconds) as client:
            return await self._collect_with_fetcher(sources, checked_at, client.get_text)

    async def _collect_with_fetcher(
        self,
        sources: list[SourceConfig],
        checked_at: datetime,
        fetch_text: Callable[[str], Awaitable[str]],
    ) -> CollectionBatch:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def run_source(
            source: SourceConfig,
        ) -> tuple[str, list[ArticleCandidate] | None, str | None]:
            async with semaphore:
                try:
                    content = await fetch_text(str(source.url))
                    candidates = await parse_feed(source, content, checked_at)
                    log_event(
                        logger,
                        logging.INFO,
                        "source_collection_succeeded",
                        source_id=source.id,
                        article_count=len(candidates),
                    )
                    return source.id, candidates, None
                except Exception as exc:
                    error = f"{type(exc).__name__}: {str(exc)[:300]}"
                    log_event(
                        logger,
                        logging.ERROR,
                        "source_collection_failed",
                        source_id=source.id,
                        error=error,
                    )
                    return source.id, None, error

        results = await asyncio.gather(*(run_source(source) for source in sources))
        batch = CollectionBatch()
        for source_id, candidates, error in results:
            if candidates is not None:
                batch.successful_sources += 1
                batch.candidates.extend(candidates)
            else:
                batch.failed_sources += 1
                batch.errors[source_id] = error or "unknown error"
        return batch
