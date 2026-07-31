from __future__ import annotations

from dataclasses import dataclass

from src.models import Article, ArticleCandidate
from src.utils.text import content_similarity, stable_hash, title_similarity


@dataclass(frozen=True)
class DuplicateMatch:
    article: Article
    reason: str
    similarity: float


def find_duplicate(
    candidate: ArticleCandidate,
    existing: list[Article],
    *,
    title_threshold: float = 0.88,
    content_threshold: float = 0.90,
) -> DuplicateMatch | None:
    candidate_url_hash = stable_hash(candidate.canonical_url)
    for article in existing:
        if article.url_hash == candidate_url_hash:
            return DuplicateMatch(article, "url", 1.0)

    best: DuplicateMatch | None = None
    for article in existing:
        title_score = title_similarity(candidate.title_original, article.title_original)
        content_score = content_similarity(
            candidate.excerpt_original, article.excerpt_original
        )
        if title_score >= title_threshold:
            match = DuplicateMatch(article, "title", title_score)
        elif content_score >= content_threshold:
            match = DuplicateMatch(article, "content", content_score)
        else:
            continue
        if best is None or match.similarity > best.similarity:
            best = match
    return best


def related_source(candidate: ArticleCandidate) -> dict[str, str]:
    return {
        "source_id": candidate.source_id,
        "source_name": candidate.source_name,
        "url": candidate.canonical_url,
    }
