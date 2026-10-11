# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Render stored instants in Moscow with an unambiguous twelve-hour clock."""

from datetime import datetime
from zoneinfo import ZoneInfo

TIMEZONE = "Europe/Moscow"
HALF_DAY = 12


def local_time(value: str) -> str:
    """Format an aware ISO timestamp for an artist-facing message.

    Args:
        value (str): Aware ISO timestamp, or empty for an absent deadline.

    Returns:
        str: Date, twelve-hour AM/PM time and Moscow label; absent dates are marked.

    Raises:
        ValueError: The timestamp is malformed or lacks a UTC offset.
        KeyError: Moscow timezone data is unavailable.
        OverflowError: Conversion exceeds supported datetime bounds.
    """  # noqa: DOC503 - Date parsing and ZoneInfo errors propagate.
    if not value:
        return "не задан"
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        message = "Display timestamps must include a UTC offset."
        raise ValueError(message)
    moment = moment.astimezone(ZoneInfo(TIMEZONE))
    period = "AM" if moment.hour < HALF_DAY else "PM"
    return (
        f"{moment:%d.%m.%Y} {moment.hour % HALF_DAY or HALF_DAY}:"
        f"{moment.minute:02d} {period} МСК"  # noqa: RUF001 - Russian Moscow abbreviation.
    )
