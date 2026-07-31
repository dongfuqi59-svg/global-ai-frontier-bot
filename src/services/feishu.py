from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import random
import time
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from src.models import Article
from src.utils.text import sanitize_untrusted_text
from src.utils.time import SHANGHAI, ensure_utc
from src.utils.url import UnsafeUrlError, normalize_url

MAX_CARD_BYTES = 28_000
ALLOWED_FEISHU_HOSTS = {"open.feishu.cn", "open.larksuite.com"}


class FeishuPublishError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def generate_feishu_signature(timestamp: int, secret: str) -> str:
    string_to_sign = f"{timestamp}\n{secret}".encode()
    digest = hmac.new(string_to_sign, b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def build_digest_payload(
    digest_date: str,
    window_start: datetime,
    window_end: datetime,
    articles: list[Article],
) -> dict[str, Any]:
    start = ensure_utc(window_start).astimezone(SHANGHAI).strftime("%m-%d %H:%M")
    end = ensure_utc(window_end).astimezone(SHANGHAI).strftime("%m-%d %H:%M")
    elements: list[dict[str, Any]] = [
        {
            "tag": "markdown",
            "content": f"**统计范围：{start} - {end}（北京时间）**",
        },
        {"tag": "hr"},
    ]
    if not articles:
        elements.append(
            {
                "tag": "markdown",
                "content": "今日暂无符合时间窗口与质量标准的新资讯。",
            }
        )
    else:
        domestic = [
            article for article in articles if article.source_region == "domestic"
        ]
        foreign = [
            article for article in articles if article.source_region == "foreign"
        ]
        _append_section(elements, "国内 AI 资讯", domestic, expected_count=10)
        _append_section(elements, "国外 AI 资讯", foreign, expected_count=10)
    elements.extend(
        [
            {"tag": "hr"},
            {
                "tag": "note",
                "elements": [
                    {
                        "tag": "plain_text",
                        "content": (
                            f"共收录 {len(articles)} 条，数据截至 {end}。"
                            "仅覆盖截至该时间已公开且成功获取的来源；"
                            "英文来源已用中文摘要模板呈现。"
                        ),
                    }
                ],
            },
        ]
    )
    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {
                    "tag": "plain_text",
                    "content": f"全球 AI 前沿日报 | {digest_date}",
                },
            },
            "elements": elements,
        },
    }
    if len(json.dumps(payload, ensure_ascii=False).encode()) > MAX_CARD_BYTES:
        raise ValueError("digest card exceeds Feishu payload size limit")
    return payload


def _append_section(
    elements: list[dict[str, Any]],
    title: str,
    articles: list[Article],
    *,
    expected_count: int,
) -> None:
    elements.append({"tag": "markdown", "content": f"**{title}（{len(articles)} 条）**"})
    if not articles:
        elements.append(
            {
                "tag": "markdown",
                "content": "当前没有符合时间窗口与质量标准的资讯。",
            }
        )
        return
    if len(articles) < expected_count:
        elements.append(
            {
                "tag": "markdown",
                "content": (
                    f"当前可用来源只筛出 {len(articles)} 条，"
                    f"不足 {expected_count} 条时不使用低质量内容凑数。"
                ),
            }
        )
    for index, article in enumerate(articles, 1):
        elements.append(
            {
                "tag": "markdown",
                "content": _render_article(index, article),
            }
        )
    elements.append({"tag": "hr"})


def _render_article(index: int, article: Article) -> str:
    url = normalize_url(article.canonical_url)
    title = _escape_markdown(_display_title(article))[:160]
    summary = _escape_markdown(_display_summary(article))[:320]
    why = _escape_markdown(article.why_it_matters)[:220]
    source = _escape_markdown(article.source_name)[:100]
    category = _escape_markdown(_category_zh(str(article.category)))[:50]
    return (
        f"**{index}. {title}**\n"
        f"{summary}\n"
        f"**为什么重要：** {why}\n"
        f"{category} | {source} | {article.published_at_display}\n"
        f"[查看原文]({url})"
    )


def _display_title(article: Article) -> str:
    if _contains_cjk(article.title_zh):
        return article.title_zh
    if article.source_type == "arxiv":
        return "新的 AI 研究论文已发布"
    if article.source_type == "github_release":
        return f"{article.source_name} 发布开源项目新版本"
    return f"{article.source_name} 发布新的 AI 资讯"


def _display_summary(article: Article) -> str:
    raw = " ".join(article.summary_zh[:2])
    if _contains_cjk(raw):
        return raw
    return (
        f"该消息来自{article.source_name}，系统已按"
        f"{_category_zh(str(article.category))}方向纳入日报。"
        "原文为英文，详情请点击原文查看。"
    )


def _contains_cjk(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _category_zh(category: str) -> str:
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
    return values.get(category, "综合动态")


def _escape_markdown(value: str) -> str:
    clean = sanitize_untrusted_text(value, 1200)
    for char in ("\\", "`", "*", "[", "]", "(", ")"):
        clean = clean.replace(char, f"\\{char}")
    return clean


class FeishuClient:
    def __init__(
        self,
        webhook_url: str,
        signing_secret: str,
        timeout_seconds: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        try:
            normalized = normalize_url(webhook_url)
        except UnsafeUrlError as exc:
            raise ValueError("invalid Feishu webhook URL") from exc
        if urlsplit(normalized).hostname not in ALLOWED_FEISHU_HOSTS:
            raise ValueError("Feishu webhook host is not allowed")
        self._webhook_url = normalized
        self._secret = signing_secret
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10)),
            follow_redirects=False,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def send(self, message_payload: dict[str, Any]) -> str | None:
        payload = dict(message_payload)
        timestamp = int(time.time())
        if self._secret:
            payload["timestamp"] = str(timestamp)
            payload["sign"] = generate_feishu_signature(timestamp, self._secret)
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = await self._client.post(
                    self._webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                body = response.json()
                code = body.get("code", body.get("StatusCode"))
                if code not in (0, "0"):
                    message = str(body.get("msg", body.get("StatusMessage", "unknown")))
                    raise FeishuPublishError(str(code), message[:300])
                data = body.get("data") or {}
                return data.get("message_id") if isinstance(data, dict) else None
            except FeishuPublishError:
                raise
            except (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.HTTPStatusError,
                json.JSONDecodeError,
                ValueError,
            ) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status < 500 and status not in {408, 425, 429}:
                        break
                if attempt < 2:
                    await asyncio.sleep((2**attempt) * 0.5 + random.uniform(0, 0.25))
        detail = type(last_error).__name__ if last_error else "UnknownError"
        if isinstance(last_error, httpx.HTTPStatusError):
            detail = f"{detail} status={last_error.response.status_code}"
        raise FeishuPublishError("NETWORK_ERROR", f"Feishu request failed: {detail}")
