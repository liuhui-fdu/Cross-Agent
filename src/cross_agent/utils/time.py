"""Time parsing and decay helpers for memory lifecycle decisions."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    normalized = re.sub(r"\s*\([A-Za-z]{3,9}\)\s*", " ", text).strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        parsed = None
    if parsed is None:
        formats = (
            "%Y/%m/%d %H:%M",
            "%Y/%m/%d",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        )
        for fmt in formats:
            try:
                parsed = datetime.strptime(normalized, fmt)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def compare_timestamps(left: str | None, right: str | None) -> int | None:
    """Return -1, 0, 1 for left vs right, or None when either is unknown."""

    left_time = parse_datetime(left)
    right_time = parse_datetime(right)
    if left_time is None or right_time is None:
        return None
    if left_time < right_time:
        return -1
    if left_time > right_time:
        return 1
    return 0


def temporal_decay_score(
    value: str | None,
    half_life_days: float,
    reference: datetime | None = None,
) -> float:
    parsed = parse_datetime(value)
    if parsed is None or half_life_days <= 0:
        return 0.60
    now = reference or datetime.utcnow()
    age_days = max(0.0, (now - parsed).total_seconds() / 86400.0)
    return math.exp(-math.log(2.0) * age_days / half_life_days)


def is_implausibly_future(
    value: str | None,
    max_future_skew_days: int,
    reference: datetime | None = None,
) -> bool:
    parsed = parse_datetime(value)
    if parsed is None:
        return False
    now = reference or datetime.utcnow()
    skew_days = (parsed - now).total_seconds() / 86400.0
    return skew_days > max_future_skew_days
