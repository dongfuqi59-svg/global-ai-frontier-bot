from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from pydantic import BaseModel

from src.models import Article, Delivery, Digest
from src.utils.time import ensure_utc, parse_iso_utc, to_iso_utc


def _serialize(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _serialize(value.model_dump())
    if isinstance(value, datetime):
        return to_iso_utc(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {
            key: _serialize(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value


class DynamoRepository:
    def __init__(
        self,
        articles_table: str,
        digests_table: str,
        deliveries_table: str,
        *,
        region_name: str,
        dynamodb_resource: Any = None,
    ) -> None:
        resource = dynamodb_resource or boto3.resource(
            "dynamodb", region_name=region_name
        )
        self._articles = resource.Table(articles_table)
        self._digests = resource.Table(digests_table)
        self._deliveries = resource.Table(deliveries_table)

    async def get_article(self, article_id: str) -> Article | None:
        response = self._articles.get_item(Key={"article_id": article_id})
        item = response.get("Item")
        return Article.model_validate(item) if item else None

    async def put_article(self, article: Article) -> None:
        self._articles.put_item(Item=_serialize(article))

    async def touch_article(self, article_id: str, checked_at: datetime) -> None:
        self._articles.update_item(
            Key={"article_id": article_id},
            UpdateExpression="SET last_checked_at = :checked_at",
            ExpressionAttributeValues={":checked_at": to_iso_utc(checked_at)},
        )

    async def query_articles(
        self, window_start: datetime, window_end: datetime
    ) -> list[Article]:
        start = to_iso_utc(window_start)
        end = to_iso_utc(window_end)
        query: dict[str, Any] = {
            "IndexName": "PublishedAtIndex",
            "KeyConditionExpression": Key("published_partition").eq("ARTICLE")
            & Key("published_at").between(start, end),
        }
        items: list[dict[str, Any]] = []
        while True:
            response = self._articles.query(**query)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query["ExclusiveStartKey"] = last_key
        articles = [
            Article.model_validate(item)
            for item in items
            if parse_iso_utc(str(item["published_at"])) > ensure_utc(window_start)
        ]
        return articles

    async def add_related_source(
        self, article_id: str, related: dict[str, str]
    ) -> None:
        self._articles.update_item(
            Key={"article_id": article_id},
            UpdateExpression=(
                "SET related_sources = list_append("
                "if_not_exists(related_sources, :empty), :related)"
            ),
            ExpressionAttributeValues={
                ":empty": [],
                ":related": [_serialize(related)],
            },
        )

    async def mark_duplicate(self, article_id: str) -> None:
        self._articles.update_item(
            Key={"article_id": article_id},
            UpdateExpression="SET processing_status = :status",
            ExpressionAttributeValues={":status": "REJECTED"},
        )

    async def get_digest(self, digest_date: str) -> Digest | None:
        response = self._digests.get_item(Key={"digest_date": digest_date})
        item = response.get("Item")
        return Digest.model_validate(item) if item else None

    async def put_digest(self, digest: Digest) -> None:
        self._digests.put_item(Item=_serialize(digest))

    async def mark_digest_published(self, digest_date: str) -> None:
        self._digests.update_item(
            Key={"digest_date": digest_date},
            UpdateExpression="SET #status = :status",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":status": "PUBLISHED"},
        )

    async def get_delivery(self, delivery_id: str) -> Delivery | None:
        response = self._deliveries.get_item(Key={"delivery_id": delivery_id})
        item = response.get("Item")
        return Delivery.model_validate(item) if item else None

    async def claim_delivery(
        self, delivery_id: str, digest_date: str, now_utc: datetime
    ) -> Delivery | None:
        current = await self.get_delivery(delivery_id)
        now = ensure_utc(now_utc)
        if current is None:
            delivery = Delivery(
                delivery_id=delivery_id,
                digest_date=digest_date,
                status="IN_PROGRESS",
                attempt_count=1,
                last_attempt_at=now,
            )
            try:
                self._deliveries.put_item(
                    Item=_serialize(delivery),
                    ConditionExpression="attribute_not_exists(delivery_id)",
                )
                return delivery
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
                return None

        if current.status == "SUCCEEDED":
            return None
        if (
            current.status == "IN_PROGRESS"
            and current.last_attempt_at
            and ensure_utc(current.last_attempt_at) > now - timedelta(seconds=60)
        ):
            return None

        condition = "#status = :expected_status"
        values: dict[str, Any] = {
            ":status": "IN_PROGRESS",
            ":expected_status": current.status,
            ":now": to_iso_utc(now),
            ":digest_date": digest_date,
            ":channel": "feishu",
            ":one": 1,
        }
        if current.last_attempt_at:
            condition += " AND last_attempt_at = :expected_last"
            values[":expected_last"] = to_iso_utc(current.last_attempt_at)
        else:
            condition += " AND attribute_not_exists(last_attempt_at)"
        try:
            response = self._deliveries.update_item(
                Key={"delivery_id": delivery_id},
                UpdateExpression=(
                    "SET #status = :status, last_attempt_at = :now, "
                    "digest_date = :digest_date, channel = :channel "
                    "ADD attempt_count :one"
                ),
                ConditionExpression=condition,
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues=values,
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
        return Delivery.model_validate(response["Attributes"])

    async def complete_delivery(
        self,
        delivery_id: str,
        *,
        succeeded: bool,
        message_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        self._deliveries.update_item(
            Key={"delivery_id": delivery_id},
            UpdateExpression=(
                "SET #status = :status, message_id = :message_id, "
                "error_code = :error_code, error_message = :error_message"
            ),
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":status": "SUCCEEDED" if succeeded else "FAILED",
                ":message_id": message_id or "",
                ":error_code": error_code or "",
                ":error_message": (error_message or "")[:1000],
            },
        )
