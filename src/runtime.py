from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from src.collectors.manager import SourceCollector
from src.config import Settings, load_sources
from src.repositories.base import Repository
from src.services.analysis import AnalysisService
from src.services.collection import CollectionService
from src.services.digest import DigestService
from src.services.feishu import FeishuClient
from src.services.llm import OpenAICompatibleClient
from src.utils.logging import log_event
from src.utils.time import utc_now

logger = logging.getLogger(__name__)
SUPPORTED_ACTIONS = {
    "collect",
    "final_collect",
    "prepare_digest",
    "publish_digest",
    "retry_publish_1",
    "retry_publish_2",
}


async def run_action(
    action: str,
    settings: Settings,
    repository: Repository,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    log_event(logger, logging.INFO, "action_started", action=action)
    llm_client = (
        OpenAICompatibleClient(
            settings.llm_base_url,
            settings.llm_api_key,
            settings.llm_model,
            settings.llm_timeout_seconds,
        )
        if settings.llm_enabled
        else None
    )
    now_utc = now or utc_now()
    feishu_client: FeishuClient | None = None
    try:
        if action in {"collect", "final_collect"}:
            collector = SourceCollector(
                settings.collect_concurrency, settings.http_timeout_seconds
            )
            service = CollectionService(
                repository,
                collector,
                AnalysisService(llm_client),
                settings.article_retention_days,
            )
            result = await service.run(
                load_sources(settings.sources_config_path), now_utc
            )
            response: dict[str, Any] = {
                "action": action,
                "status": "ok",
                **result.__dict__,
            }
        elif action == "prepare_digest":
            digest = await DigestService(
                repository,
                settings.digest_item_limit,
                llm_client=llm_client,
            ).prepare(now_utc)
            response = {
                "action": action,
                "status": "ok",
                "digest_date": digest.digest_date,
                "article_count": len(digest.article_ids),
            }
        else:
            if not settings.feishu_webhook_url:
                raise ValueError("FEISHU_WEBHOOK_URL is required for publishing")
            feishu_client = FeishuClient(
                settings.feishu_webhook_url,
                settings.feishu_signing_secret,
                settings.http_timeout_seconds,
            )
            status = await DigestService(
                repository, settings.digest_item_limit
            ).publish(
                now_utc,
                feishu_client,
                retry_only=action.startswith("retry_publish_"),
            )
            response = {"action": action, "status": status}
        log_event(logger, logging.INFO, "action_completed", **response)
        return response
    finally:
        if feishu_client is not None:
            await feishu_client.aclose()
        if llm_client is not None:
            await llm_client.aclose()
