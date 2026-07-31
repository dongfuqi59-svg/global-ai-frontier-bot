from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from src.config import Settings
from src.repositories.file import JsonFileRepository
from src.runtime import SUPPORTED_ACTIONS, run_action
from src.utils.logging import configure_logging, log_event, redact

logger = logging.getLogger(__name__)
DAILY_ACTIONS = ("final_collect", "prepare_digest")
PUBLISH_ACTIONS = ("publish_digest", "retry_publish_1", "retry_publish_2")


async def run_cli_action(action: str, settings: Settings) -> dict[str, Any]:
    repository = JsonFileRepository(settings.state_file_path)
    if action == "daily":
        results: list[dict[str, Any]] = []
        for daily_action in DAILY_ACTIONS:
            results.append(await run_action(daily_action, settings, repository))
        published = False
        last_error: Exception | None = None
        for publish_action in PUBLISH_ACTIONS:
            try:
                result = await run_action(publish_action, settings, repository)
            except Exception as exc:
                last_error = exc
                log_event(
                    logger,
                    logging.ERROR,
                    "daily_publish_attempt_failed",
                    action=publish_action,
                    error_type=type(exc).__name__,
                    error_message=str(redact(str(exc))),
                )
                results.append(
                    {
                        "action": publish_action,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                    }
                )
                continue
            results.append(result)
            if result["status"] in {"PUBLISHED", "SKIPPED_ALREADY_SUCCEEDED"}:
                published = True
                break
        if not published:
            if last_error is not None:
                raise last_error
            raise RuntimeError("digest was not published")
        return {"action": action, "status": "ok", "results": results}
    return await run_action(action, settings, repository)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the AI frontier Feishu bot locally or in GitHub Actions."
    )
    parser.add_argument(
        "--action",
        required=True,
        choices=sorted([*SUPPORTED_ACTIONS, "daily"]),
        help="Action to run. daily runs final_collect, prepare_digest and publish retries.",
    )
    parser.add_argument(
        "--state-file",
        default=None,
        help="JSON state file path. Defaults to STATE_FILE_PATH or data/bot-state.json.",
    )
    parser.add_argument(
        "--env-file",
        default=".env",
        help="Optional .env file to load before reading settings.",
    )
    parser.add_argument(
        "--no-env-file",
        action="store_true",
        help="Disable loading the local .env file.",
    )
    args = parser.parse_args()

    if not args.no_env_file:
        load_env_file(Path(args.env_file))
    if args.state_file:
        os.environ["STATE_FILE_PATH"] = str(args.state_file)

    settings = Settings.from_env()
    configure_logging(settings.log_level)
    result = asyncio.run(run_cli_action(args.action, settings))
    print(json.dumps(redact(result), ensure_ascii=False, sort_keys=True))


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key, separator, value = line.partition("=")
        key = key.strip()
        if not separator or not key or key in os.environ:
            continue
        os.environ[key] = _strip_env_value(value.strip())


def _strip_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


if __name__ == "__main__":
    main()
