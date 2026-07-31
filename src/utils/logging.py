from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

SENSITIVE_KEYS = re.compile(
    r"(api[_-]?key|authorization|secret|token|webhook|password)", re.IGNORECASE
)
WEBHOOK_PATTERN = re.compile(
    r"https://open\.(?:feishu\.cn|larksuite\.com)"
    r"/open-apis/bot/v2/hook/[A-Za-z0-9_-]+"
)


def redact(value: Any, key: str = "") -> Any:
    if SENSITIVE_KEYS.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {item_key: redact(item, item_key) for item_key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return WEBHOOK_PATTERN.sub("[REDACTED_WEBHOOK]", value)
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        details = getattr(record, "details", None)
        if isinstance(details, dict):
            payload.update(redact(details))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(redact(payload), ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.handlers[:] = [handler]


def log_event(
    logger: logging.Logger, level: int, event: str, **details: Any
) -> None:
    logger.log(level, event, extra={"details": {"event": event, **details}})
