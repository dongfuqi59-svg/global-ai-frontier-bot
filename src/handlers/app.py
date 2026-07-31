from __future__ import annotations

import asyncio
from typing import Any

from src.config import Settings
from src.repositories.dynamo import DynamoRepository
from src.runtime import SUPPORTED_ACTIONS, run_action
from src.utils.logging import configure_logging


async def handle(event: dict[str, Any]) -> dict[str, Any]:
    settings = Settings.from_env()
    configure_logging(settings.log_level)
    action = str(event.get("action", ""))
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    repository = DynamoRepository(
        settings.articles_table,
        settings.digests_table,
        settings.deliveries_table,
        region_name=settings.aws_region,
    )
    return await run_action(action, settings, repository)


def lambda_handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    return asyncio.run(handle(event))
