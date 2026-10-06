# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Deliver durable reminders without calling a language model."""

import json
import re
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from .network import api, object_map
from .store import connect, read_orders

if TYPE_CHECKING:
    from collections.abc import Callable


def deliver_due(path: Path, send: Callable[[str], None], now: datetime) -> int:
    """Send due reminders under a transaction to serialize edits and other workers.

    Commit each delivery separately. Retry failures with exponential backoff capped
    at one hour. A lost successful response can still cause a duplicate on retry;
    Telegram does not offer an idempotency key for sendMessage.

    Args:
        path (Path): Orders database filename.
        send (Callable[[str], None]): Bounded sender; raise RuntimeError on failure.
        now (datetime): Current aware timestamp used for due checks and retry times.

    Returns:
        int: Number of successfully acknowledged sends during this tick.

    Raises:
        ValueError: Now is naive or the database schema version is unsupported.
        OSError: The data directory cannot be created.
        sqlite3.Error: Reading or committing the database fails.
        KeyError: Stored timezone or required order field is invalid.
        OverflowError: The retry timestamp exceeds datetime limits.
    """  # noqa: DOC503 - Storage and date conversion errors propagate.
    if now.tzinfo is None:
        message = "Reminder clock must include a timezone."
        raise ValueError(message)
    stamp = now.astimezone(UTC).isoformat(timespec="seconds")
    sent = 0
    with closing(connect(path)) as database:
        identifiers = [item["id"] for item in read_orders(database)]
        for identifier in identifiers:
            with database:
                database.execute("BEGIN IMMEDIATE")
                row = database.execute(
                    "SELECT * FROM orders WHERE id=? AND status='open' AND sent_at='' "
                    "AND remind_at<>'' AND remind_at<=? AND next_attempt<=?",
                    (identifier, stamp, stamp),
                ).fetchone()
                if row is None:
                    continue
                local_due = (
                    datetime.fromisoformat(row["due"])
                    .astimezone(ZoneInfo(row["timezone"]))
                    .strftime("%d.%m.%Y %H:%M %Z")
                    if row["due"]
                    else "не задан"
                )
                # These labels are intentionally Russian, not mixed-script identifiers.
                text = (
                    f"Джесс на связи. {row['title']}\n"
                    f"Срок: {local_due}\nЗаказ: {identifier}"  # noqa: RUF001
                )
                try:
                    send(text)
                except RuntimeError:
                    delay = min(3600, 60 * 2 ** min(int(row["attempts"]), 6))
                    retry = (now.astimezone(UTC) + timedelta(seconds=delay)).isoformat(
                        timespec="seconds"
                    )
                    database.execute(
                        "UPDATE orders SET attempts=attempts+1,next_attempt=?,"
                        "last_error='delivery_failed' WHERE id=?",
                        (retry, identifier),
                    )
                else:
                    database.execute(
                        "UPDATE orders SET sent_at=?,attempts=attempts+1,last_error='' "
                        "WHERE id=?",
                        (stamp, identifier),
                    )
                    sent += 1
    return sent


def tick(profile: Path) -> int:
    """Run one no-agent cron tick using only this profile's token and group.

    Args:
        profile (Path): Named Hermes profile containing .env and plugin settings.

    Returns:
        int: Number of acknowledged reminders.

    Raises:
        ValueError: Configuration, JSON, group restriction or schema is invalid.
        KeyError: A required configuration value or stored timezone is missing.
        OSError: Configuration or database files cannot be accessed.
        UnicodeError: Configuration text is not valid UTF-8.
        sqlite3.Error: Database operations fail.
        OverflowError: The retry timestamp exceeds datetime limits.
    """  # noqa: DOC503 - Includes delegated storage and parsing errors.
    settings = object_map(
        json.loads((profile / "plugins/drawer/settings.json").read_text("utf-8"))
    )
    credentials = dotenv_values(profile / ".env", interpolate=False)
    token = credentials.get("TELEGRAM_BOT_TOKEN") or ""
    chat = credentials.get("TELEGRAM_ALLOWED_CHATS") or ""
    if not re.fullmatch(r"-[1-9]\d*", chat) or not re.fullmatch(r"\d+:[\w-]+", token):
        message = "A bot token and exactly one negative group id are required."
        raise ValueError(message)

    def send(text: str) -> None:
        """Send one plain-text message to the configured group only.

        Args:
            text (str): Reminder text without Markdown parsing.

        Returns:
            None: Telegram acknowledged the send.

        Raises:
            RuntimeError: Transport fails or Telegram rejects the message.
        """
        try:
            result = api(
                f"https://api.telegram.org/bot{token}/sendMessage",
                {"chat_id": chat, "text": text},
            )
            if result.get("ok") is not True:
                message = "Telegram rejected the reminder."
                raise RuntimeError(message)
        except (ValueError, TypeError, UnicodeError) as exc:
            message = "Invalid Telegram response."
            raise RuntimeError(message) from exc

    return deliver_due(Path(str(settings["database"])), send, datetime.now(UTC))
