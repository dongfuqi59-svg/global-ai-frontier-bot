from __future__ import annotations

import os
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, Field, model_validator

from src.models import SourceConfig


class Settings(BaseModel):
    aws_region: str = "ap-southeast-1"
    app_timezone: str = "Asia/Shanghai"
    articles_table: str = "ai-frontier-articles"
    digests_table: str = "ai-frontier-digests"
    deliveries_table: str = "ai-frontier-deliveries"
    feishu_webhook_url: str = ""
    feishu_signing_secret: str = ""
    digest_item_limit: int = Field(default=10, ge=5, le=20)
    article_retention_days: int = Field(default=180, ge=1)
    collect_concurrency: int = Field(default=8, ge=1, le=20)
    http_timeout_seconds: float = Field(default=20, gt=0, le=120)
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=45, gt=0, le=180)
    llm_enabled: bool = False
    log_level: str = "INFO"
    sources_config_path: str = "config/sources.yaml"
    state_file_path: str = "data/bot-state.json"

    @model_validator(mode="after")
    def validate_runtime(self) -> "Settings":
        ZoneInfo(self.app_timezone)
        if self.llm_enabled and not (
            self.llm_base_url and self.llm_api_key and self.llm_model
        ):
            self.llm_enabled = False
        return self

    @classmethod
    def from_env(cls) -> "Settings":
        def value(name: str, default: str) -> str:
            return os.environ.get(name, default)

        return cls(
            aws_region=value("AWS_REGION", "ap-southeast-1"),
            app_timezone=value("APP_TIMEZONE", "Asia/Shanghai"),
            articles_table=value("ARTICLES_TABLE", "ai-frontier-articles"),
            digests_table=value("DIGESTS_TABLE", "ai-frontier-digests"),
            deliveries_table=value("DELIVERIES_TABLE", "ai-frontier-deliveries"),
            feishu_webhook_url=value("FEISHU_WEBHOOK_URL", ""),
            feishu_signing_secret=value("FEISHU_SIGNING_SECRET", ""),
            digest_item_limit=int(value("DIGEST_ITEM_LIMIT", "10")),
            article_retention_days=int(value("ARTICLE_RETENTION_DAYS", "180")),
            collect_concurrency=int(value("COLLECT_CONCURRENCY", "8")),
            http_timeout_seconds=float(value("HTTP_TIMEOUT_SECONDS", "20")),
            llm_base_url=value("LLM_BASE_URL", ""),
            llm_api_key=value("LLM_API_KEY", ""),
            llm_model=value("LLM_MODEL", ""),
            llm_timeout_seconds=float(value("LLM_TIMEOUT_SECONDS", "45")),
            llm_enabled=value("LLM_ENABLED", "false").lower() == "true",
            log_level=value("LOG_LEVEL", "INFO").upper(),
            sources_config_path=value("SOURCES_CONFIG_PATH", "config/sources.yaml"),
            state_file_path=value("STATE_FILE_PATH", "data/bot-state.json"),
        )


def load_sources(path: str | Path) -> list[SourceConfig]:
    with Path(path).open(encoding="utf-8") as file:
        payload = yaml.safe_load(file) or {}
    return [
        SourceConfig.model_validate(item)
        for item in payload.get("sources", [])
        if item.get("enabled", True)
    ]
