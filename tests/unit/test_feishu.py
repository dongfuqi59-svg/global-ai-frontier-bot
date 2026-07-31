from datetime import datetime, timezone

import httpx
import pytest

from src.services.feishu import (
    FeishuClient,
    FeishuPublishError,
    build_digest_payload,
    generate_feishu_signature,
)
from src.utils.url import UnsafeUrlError

UTC = timezone.utc


def test_feishu_signature() -> None:
    assert (
        generate_feishu_signature(1_700_000_000, "test-secret")
        == "mbm4Y4oluIPQ00qlBIhX8vAZ0EKv3nw0LuTb91jPL84="
    )


def test_card_rejects_private_article_link(article_factory) -> None:
    article = article_factory("private")
    article.canonical_url = "http://169.254.169.254/latest/meta-data/"
    with pytest.raises(UnsafeUrlError):
        build_digest_payload(
            "2026-07-31",
            datetime(2026, 7, 30, 1, 50, tzinfo=UTC),
            datetime(2026, 7, 31, 1, 50, tzinfo=UTC),
            [article],
        )


def test_card_groups_domestic_and_foreign_items(article_factory) -> None:
    domestic = article_factory("cn", source_region="domestic")
    foreign = article_factory("global", source_region="foreign")
    foreign.title_zh = "OpenAI releases a model"
    foreign.summary_zh = ["OpenAI releases a model for developers."]
    payload = build_digest_payload(
        "2026-07-31",
        datetime(2026, 7, 30, 1, 50, tzinfo=UTC),
        datetime(2026, 7, 31, 1, 50, tzinfo=UTC),
        [domestic, foreign],
    )
    content = str(payload)

    assert "国内 AI 资讯" in content
    assert "国外 AI 资讯" in content
    assert "发布新的 AI 资讯" in content
    assert "OpenAI releases a model for developers." not in content


@pytest.mark.asyncio
async def test_feishu_non_success_body_is_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 19021, "msg": "signature error"})

    client = FeishuClient(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test",
        "",
        1,
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(FeishuPublishError) as exc_info:
            await client.send({"msg_type": "text", "content": {"text": "test"}})
        assert exc_info.value.code == "19021"
    finally:
        await client.aclose()
