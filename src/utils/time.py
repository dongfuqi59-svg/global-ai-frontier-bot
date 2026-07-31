from __future__ import annotations

import calendar
from datetime import date, datetime, time, timedelta, timezone
from email.utils import parsedate_to_datetime
from time import struct_time
from zoneinfo import ZoneInfo

UTC = timezone.utc
SHANGHAI = ZoneInfo("Asia/Shanghai")


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime is not allowed")
    return value.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def to_iso_utc(value: datetime) -> str:
    return ensure_utc(value).isoformat().replace("+00:00", "Z")


def parse_iso_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ensure_utc(parsed)


def parse_source_datetime(
    raw: str | None, parsed_tuple: struct_time | None = None
) -> datetime | None:
    if parsed_tuple is not None:
        return datetime.fromtimestamp(calendar.timegm(parsed_tuple), UTC)
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def beijing_display(value: datetime | None) -> str:
    if value is None:
        return "时间未知"
    return ensure_utc(value).astimezone(SHANGHAI).strftime("%Y-%m-%d %H:%M")


def digest_window(reference_utc: datetime) -> tuple[str, datetime, datetime]:
    local = ensure_utc(reference_utc).astimezone(SHANGHAI)
    digest_day = local.date()
    end_local = datetime.combine(digest_day, time(9, 50), SHANGHAI)
    start_local = end_local - timedelta(days=1)
    return (
        digest_day.isoformat(),
        start_local.astimezone(UTC),
        end_local.astimezone(UTC),
    )


def digest_date(reference_utc: datetime) -> str:
    return ensure_utc(reference_utc).astimezone(SHANGHAI).date().isoformat()


def publication_is_valid(
    published_at: datetime | None,
    now_utc: datetime,
    *,
    window_start: datetime | None = None,
    window_end: datetime | None = None,
) -> bool:
    if published_at is None:
        return False
    published = ensure_utc(published_at)
    now = ensure_utc(now_utc)
    if published > now + timedelta(minutes=5):
        return False
    if window_start is not None and published <= ensure_utc(window_start):
        return False
    return not (window_end is not None and published > ensure_utc(window_end))


def expiry_epoch(first_seen_at: datetime, retention_days: int) -> int:
    return int((ensure_utc(first_seen_at) + timedelta(days=retention_days)).timestamp())


def local_date(value: datetime) -> date:
    return ensure_utc(value).astimezone(SHANGHAI).date()
