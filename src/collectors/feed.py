from __future__ import annotations

from datetime import datetime
from typing import Any

import feedparser

from src.models import ArticleCandidate, SourceConfig
from src.utils.text import sanitize_untrusted_text
from src.utils.time import parse_source_datetime
from src.utils.url import UnsafeUrlError, ensure_public_url


async def parse_feed(
    source: SourceConfig,
    content: str,
    checked_at: datetime,
) -> list[ArticleCandidate]:
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"invalid feed: {type(parsed.bozo_exception).__name__}")

    candidates: list[ArticleCandidate] = []
    for entry in parsed.entries[:50]:
        candidate = await _entry_to_candidate(source, entry, checked_at)
        if candidate is not None:
            candidates.append(candidate)
    return candidates


async def _entry_to_candidate(
    source: SourceConfig,
    entry: dict[str, Any],
    checked_at: datetime,
) -> ArticleCandidate | None:
    title = sanitize_untrusted_text(entry.get("title"), 500)
    link = entry.get("link")
    if not title or not isinstance(link, str):
        return None
    try:
        canonical_url = await ensure_public_url(link)
    except (UnsafeUrlError, OSError):
        return None

    raw_time = entry.get("published") or entry.get("updated")
    parsed_time = entry.get("published_parsed") or entry.get("updated_parsed")
    published_at = parse_source_datetime(raw_time, parsed_time)
    summary = entry.get("summary") or entry.get("description") or ""
    author = sanitize_untrusted_text(entry.get("author"), 300) or None

    return ArticleCandidate(
        source_id=source.id,
        source_name=source.name,
        source_type=source.type,
        source_region=source.source_region,
        source_credibility_weight=source.credibility_weight,
        canonical_url=canonical_url,
        title_original=title,
        excerpt_original=sanitize_untrusted_text(summary, 6000),
        author=author,
        language=source.language,
        category=source.category,
        published_at=published_at,
        published_raw=sanitize_untrusted_text(raw_time, 300) or None,
        source_timezone=_timezone_hint(raw_time),
        first_seen_at=checked_at,
        last_checked_at=checked_at,
    )


def _timezone_hint(raw_time: Any) -> str | None:
    if not isinstance(raw_time, str):
        return None
    parts = raw_time.strip().split()
    if len(parts) >= 2:
        return parts[-1][:80]
    return None
