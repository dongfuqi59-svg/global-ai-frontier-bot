from datetime import datetime, timezone

import pytest

from src.models import ArticleCandidate, Category
from src.services.dedup import find_duplicate
from src.services.scoring import calculate_final_score, select_diverse_articles

UTC = timezone.utc


def _candidate(title: str, excerpt: str, url: str) -> ArticleCandidate:
    now = datetime(2026, 7, 31, 1, tzinfo=UTC)
    return ArticleCandidate(
        source_id="official",
        source_name="Official",
        source_type="rss",
        source_credibility_weight=1,
        canonical_url=url,
        title_original=title,
        excerpt_original=excerpt,
        language="en",
        category=Category.FOUNDATION_MODEL,
        published_at=now,
        first_seen_at=now,
        last_checked_at=now,
    )


def test_url_title_and_content_duplicates(article_factory) -> None:
    article = article_factory("same")
    article.canonical_url = "https://example.com/item"
    article.url_hash = __import__("hashlib").sha256(
        article.canonical_url.encode()
    ).hexdigest()
    article.title_original = "OpenAI releases Model X for developers"
    article.excerpt_original = "Model X adds a long context window and tool use."

    assert (
        find_duplicate(
            _candidate("Different", "Different", article.canonical_url), [article]
        ).reason
        == "url"
    )
    assert (
        find_duplicate(
            _candidate(
                "OpenAI releases Model X for developers!",
                "Unrelated excerpt",
                "https://other.example/title",
            ),
            [article],
        ).reason
        == "title"
    )
    assert (
        find_duplicate(
            _candidate(
                "A separate headline",
                "Model X adds a long context window and tool use.",
                "https://other.example/content",
            ),
            [article],
        ).reason
        == "content"
    )


def test_score_formula_and_range_validation() -> None:
    assert calculate_final_score(100, 80, 60, 40, 20) == 70
    with pytest.raises(ValueError):
        calculate_final_score(101, 80, 60, 40, 20)


def test_selection_enforces_diversity_and_company_limit(article_factory) -> None:
    candidates = [
        article_factory(
            f"same-{index}",
            category=(
                Category.FOUNDATION_MODEL
                if index < 3
                else Category.MULTIMODAL
            ),
            score=95 - index,
            entity="Same Company",
        )
        for index in range(5)
    ]
    candidates.extend(
        [
            article_factory(
                "research",
                category=Category.RESEARCH,
                score=80,
                entity="Research Lab",
            ),
            article_factory(
                "policy",
                category=Category.POLICY_SAFETY,
                score=79,
                entity="Regulator",
            ),
        ]
    )
    selected = select_diverse_articles(candidates, 6)
    assert len({str(item.category) for item in selected}) >= 3
    assert sum(item.entities[0] == "Same Company" for item in selected) <= 3
