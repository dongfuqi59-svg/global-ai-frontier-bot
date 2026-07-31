from __future__ import annotations

from collections import Counter
from datetime import datetime

from src.models import Article
from src.utils.text import normalized_title
from src.utils.time import ensure_utc


def calculate_final_score(
    frontier_relevance: float,
    source_credibility: float,
    industry_impact: float,
    novelty: float,
    freshness: float,
) -> float:
    values = (
        frontier_relevance,
        source_credibility,
        industry_impact,
        novelty,
        freshness,
    )
    if any(value < 0 or value > 100 for value in values):
        raise ValueError("all score components must be between 0 and 100")
    return round(
        0.30 * frontier_relevance
        + 0.25 * source_credibility
        + 0.20 * industry_impact
        + 0.15 * novelty
        + 0.10 * freshness,
        2,
    )


def freshness_score(published_at: datetime, reference_utc: datetime) -> float:
    age_hours = max(
        0.0,
        (ensure_utc(reference_utc) - ensure_utc(published_at)).total_seconds() / 3600,
    )
    return round(max(0.0, 100.0 - age_hours * (50.0 / 24.0)), 2)


def select_diverse_articles(
    candidates: list[Article],
    limit: int,
    *,
    minimum_categories: int = 3,
    max_per_topic: int = 3,
    preferred_article_ids: set[str] | None = None,
) -> list[Article]:
    preferred = preferred_article_ids or set()
    ordered = sorted(
        candidates,
        key=lambda article: (
            article.final_score + (2.0 if article.article_id in preferred else 0.0)
        ),
        reverse=True,
    )
    selected: list[Article] = []
    selected_ids: set[str] = set()
    topic_counts: Counter[str] = Counter()

    def topic_key(article: Article) -> str:
        if article.entities:
            return article.entities[0].casefold()
        if article.source_id:
            return article.source_id.casefold()
        return normalized_title(article.title_original)[:40]

    def add(article: Article) -> bool:
        topic = topic_key(article)
        if article.article_id in selected_ids or topic_counts[topic] >= max_per_topic:
            return False
        selected.append(article)
        selected_ids.add(article.article_id)
        topic_counts[topic] += 1
        return True

    available_categories = {str(article.category) for article in ordered}
    target_categories = min(minimum_categories, len(available_categories), limit)
    covered: set[str] = set()
    for article in ordered:
        category = str(article.category)
        if category in covered:
            continue
        if add(article):
            covered.add(category)
        if len(covered) >= target_categories:
            break

    for article in ordered:
        if len(selected) >= limit:
            break
        add(article)
    return selected[:limit]
