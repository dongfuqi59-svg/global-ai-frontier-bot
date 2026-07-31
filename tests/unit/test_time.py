from datetime import datetime, timedelta, timezone

from src.utils.time import (
    SHANGHAI,
    beijing_display,
    digest_window,
    publication_is_valid,
    to_iso_utc,
)

UTC = timezone.utc


def test_utc_and_beijing_conversion() -> None:
    value = datetime(2026, 7, 31, 1, 50, tzinfo=UTC)
    assert value.astimezone(SHANGHAI).hour == 9
    assert beijing_display(value) == "2026-07-31 09:50"
    assert to_iso_utc(value) == "2026-07-31T01:50:00Z"


def test_digest_window_uses_beijing_boundaries() -> None:
    digest_date, start, end = digest_window(
        datetime(2026, 7, 31, 2, 0, tzinfo=UTC)
    )
    assert digest_date == "2026-07-31"
    assert start == datetime(2026, 7, 30, 1, 50, tzinfo=UTC)
    assert end == datetime(2026, 7, 31, 1, 50, tzinfo=UTC)


def test_window_is_open_at_start_and_closed_at_end() -> None:
    now = datetime(2026, 7, 31, 2, 0, tzinfo=UTC)
    _, start, end = digest_window(now)
    assert not publication_is_valid(
        start, now, window_start=start, window_end=end
    )
    assert publication_is_valid(
        start + timedelta(microseconds=1),
        now,
        window_start=start,
        window_end=end,
    )
    assert publication_is_valid(end, now, window_start=start, window_end=end)
    assert not publication_is_valid(
        end + timedelta(microseconds=1),
        now,
        window_start=start,
        window_end=end,
    )


def test_missing_and_far_future_publication_are_rejected() -> None:
    now = datetime(2026, 7, 31, 2, 0, tzinfo=UTC)
    assert not publication_is_valid(None, now)
    assert publication_is_valid(now + timedelta(minutes=5), now)
    assert not publication_is_valid(now + timedelta(minutes=5, seconds=1), now)
