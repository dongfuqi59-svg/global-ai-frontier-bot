from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime

from src.models import (
    AnalysisResult,
    Article,
    ArticleCandidate,
    Category,
    FactCheckResult,
    ProcessingStatus,
)
from src.prompts.system import ANALYSIS_SYSTEM_PROMPT, FACT_CHECK_SYSTEM_PROMPT
from src.services.llm import LLMClient
from src.services.scoring import calculate_final_score, freshness_score
from src.utils.text import sanitize_untrusted_text, stable_hash
from src.utils.time import beijing_display, expiry_epoch

AI_TERMS = {
    " ai ",
    "artificial intelligence",
    "machine learning",
    "neural",
    "llm",
    "model",
    "agent",
    "multimodal",
    "人工智能",
    "大模型",
    "算法",
    "智能体",
    "多模态",
    "生成式",
    "生成式ai",
    "机器人",
    "算力",
    "芯片",
    "deepseek",
    "通义",
    "文心",
    "豆包",
}
SENTENCE_SPLIT = re.compile(r"(?<=[。！？.!?])\s+")


@dataclass(frozen=True)
class AnalysisOutcome:
    result: AnalysisResult
    llm_failed: bool = False
    llm_deferred: bool = False


class AnalysisService:
    def __init__(
        self,
        llm_client: LLMClient | None,
        *,
        max_llm_articles: int = 10,
        max_llm_elapsed_seconds: float = 150,
    ) -> None:
        self._llm = llm_client
        self._remaining_llm_articles = max_llm_articles
        self._max_llm_elapsed_seconds = max_llm_elapsed_seconds
        self._started_at = time.monotonic()
        self._llm_available = True

    async def analyze(self, candidate: ArticleCandidate) -> AnalysisOutcome:
        fallback = deterministic_fallback(candidate)
        if self._llm is None:
            return AnalysisOutcome(fallback)
        if (
            not self._llm_available
            or self._remaining_llm_articles <= 0
            or time.monotonic() - self._started_at >= self._max_llm_elapsed_seconds
        ):
            return AnalysisOutcome(fallback, llm_failed=True, llm_deferred=True)
        self._remaining_llm_articles -= 1
        source_data = {
            "title": candidate.title_original,
            "excerpt": candidate.excerpt_original,
            "source_name": candidate.source_name,
            "published_at": candidate.published_at,
            "configured_category": candidate.category,
        }
        try:
            analysis = await self._llm.complete_json(
                ANALYSIS_SYSTEM_PROMPT, source_data, AnalysisResult
            )
            check = await self._llm.complete_json(
                FACT_CHECK_SYSTEM_PROMPT,
                {
                    "original": source_data,
                    "title_zh": analysis.title_zh,
                    "summary_zh": analysis.summary_zh,
                    "why_it_matters": analysis.why_it_matters,
                },
                FactCheckResult,
            )
            if not check.passed:
                return AnalysisOutcome(fallback, llm_failed=True)
            checked = AnalysisResult.model_validate(
                {
                    **analysis.model_dump(),
                    "title_zh": sanitize_untrusted_text(
                        check.corrected_title_zh, 300
                    ),
                    "summary_zh": [
                        sanitize_untrusted_text(line, 500)
                        for line in check.corrected_summary_zh
                    ],
                    "why_it_matters": sanitize_untrusted_text(
                        check.corrected_why_it_matters, 500
                    ),
                    "confidence": min(analysis.confidence, check.confidence),
                }
            )
            return AnalysisOutcome(checked)
        except Exception:
            self._llm_available = False
            return AnalysisOutcome(fallback, llm_failed=True)


def deterministic_fallback(candidate: ArticleCandidate) -> AnalysisResult:
    haystack = f" {candidate.title_original} {candidate.excerpt_original} ".casefold()
    explicit_ai = any(term in haystack for term in AI_TERMS)
    trusted_ai_category = (
        candidate.category.value not in {"policy_safety", "other"}
        and candidate.source_region == "foreign"
    )
    is_frontier = explicit_ai or trusted_ai_category
    summary = _summary_lines(candidate, is_frontier)
    relevance = 65 if is_frontier else 20
    impact = 55 if is_frontier else 20
    novelty = 50 if is_frontier else 15
    return AnalysisResult(
        title_zh=_title_zh(candidate, is_frontier),
        summary_zh=summary,
        why_it_matters=(
            "该信息可能影响 AI 技术、产品或产业实践，需结合原文评估具体影响。"
            if is_frontier
            else "当前来源摘要不足以证明其属于 AI 前沿进展。"
        ),
        category=candidate.category,
        entities=[],
        frontier_relevance=relevance,
        industry_impact=impact,
        novelty=novelty,
        confidence=0.45 if is_frontier else 0.25,
        is_ai_frontier=is_frontier,
        rejection_reason=None if is_frontier else "缺少明确的 AI 前沿相关信息",
    )


def build_article(
    candidate: ArticleCandidate,
    outcome: AnalysisOutcome,
    reference_utc: datetime,
    retention_days: int,
) -> Article:
    analysis = outcome.result
    published = candidate.published_at
    freshness = freshness_score(published, reference_utc) if published else 0.0
    source_credibility = round(candidate.source_credibility_weight * 100, 2)
    final_score = calculate_final_score(
        analysis.frontier_relevance,
        source_credibility,
        analysis.industry_impact,
        analysis.novelty,
        freshness,
    )
    if not analysis.is_ai_frontier:
        status = ProcessingStatus.REJECTED
    elif outcome.llm_failed:
        status = ProcessingStatus.ANALYSIS_FAILED
    else:
        status = ProcessingStatus.PROCESSED
    url_hash = stable_hash(candidate.canonical_url)
    return Article(
        article_id=url_hash[:32],
        canonical_url=candidate.canonical_url,
        url_hash=url_hash,
        source_id=candidate.source_id,
        source_name=candidate.source_name,
        source_type=candidate.source_type,
        source_region=candidate.source_region,
        title_original=candidate.title_original,
        title_zh=analysis.title_zh,
        language=candidate.language,
        excerpt_original=candidate.excerpt_original,
        author=candidate.author,
        summary_zh=analysis.summary_zh,
        why_it_matters=analysis.why_it_matters,
        category=analysis.category,
        entities=analysis.entities,
        published_at=published,
        published_at_display=beijing_display(published),
        published_raw=candidate.published_raw,
        source_timezone=candidate.source_timezone,
        first_seen_at=candidate.first_seen_at,
        last_checked_at=candidate.last_checked_at,
        title_hash=stable_hash(candidate.title_original.casefold()),
        content_hash=stable_hash(candidate.excerpt_original.casefold()),
        related_sources=[],
        frontier_relevance=analysis.frontier_relevance,
        source_credibility=source_credibility,
        industry_impact=analysis.industry_impact,
        novelty=analysis.novelty,
        freshness=freshness,
        final_score=final_score,
        analysis_confidence=analysis.confidence,
        processing_status=status,
        expires_at=expiry_epoch(candidate.first_seen_at, retention_days),
    )


def _title_zh(candidate: ArticleCandidate, is_frontier: bool) -> str:
    if _contains_cjk(candidate.title_original):
        return sanitize_untrusted_text(candidate.title_original, 300)
    source_name = _source_name_zh(candidate)
    category = _category_zh(candidate.category)
    if not is_frontier:
        return f"【待确认】{source_name} 发布一条与 AI 相关性较弱的动态"
    if candidate.source_type == "arxiv":
        return f"【{category}】arXiv 收录一篇新的 AI 研究论文"
    if candidate.source_type == "github_release":
        return f"【{category}】{source_name} 发布开源项目新版本"
    return f"【{category}】{source_name} 发布新的 AI 资讯"


def _summary_lines(candidate: ArticleCandidate, is_frontier: bool) -> list[str]:
    if not _contains_cjk(f"{candidate.title_original} {candidate.excerpt_original}"):
        source_name = _source_name_zh(candidate)
        category = _category_zh(candidate.category)
        if is_frontier:
            return [
                f"该消息来自{source_name}，系统将其归入{category}方向。",
                "原文为英文，当前零成本模式使用中文摘要模板呈现，详情可点击原文查看。",
            ]
        return [
            f"该消息来自{source_name}，当前摘要不足以确认其属于 AI 前沿进展。",
        ]
    excerpt = sanitize_untrusted_text(candidate.excerpt_original, 1000)
    if not excerpt:
        return [sanitize_untrusted_text(candidate.title_original, 500)]
    parts = [part.strip() for part in SENTENCE_SPLIT.split(excerpt) if part.strip()]
    return [sanitize_untrusted_text(part, 500) for part in (parts or [excerpt])[:3]]


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _source_name_zh(candidate: ArticleCandidate) -> str:
    names = {
        "openai_news": "OpenAI",
        "google_ai": "Google AI",
        "deepmind_blog": "Google DeepMind",
        "microsoft_research": "Microsoft Research",
        "nvidia_ai": "NVIDIA AI",
        "arxiv_ai": "arXiv",
        "arxiv_lg": "arXiv",
        "vllm_releases": "vLLM",
        "langchain_releases": "LangChain",
        "transformers_releases": "Transformers",
        "eu_digital_ai": "欧盟数字战略",
        "ftc_news": "美国联邦贸易委员会",
        "techcrunch_ai": "TechCrunch",
        "mit_tech_ai": "MIT Technology Review",
        "ithome_ai": "IT之家",
        "36kr_ai": "36氪",
    }
    return names.get(candidate.source_id, candidate.source_name)


def _category_zh(category: Category | str) -> str:
    key = category.value if isinstance(category, Category) else str(category)
    values = {
        "foundation_model": "基础模型",
        "agent": "智能体",
        "multimodal": "多模态",
        "open_source": "开源生态",
        "infra_chip": "算力与芯片",
        "research": "研究进展",
        "product": "产品应用",
        "funding": "产业投融资",
        "policy_safety": "政策与安全",
        "other": "综合动态",
    }
    return values.get(key, "综合动态")
