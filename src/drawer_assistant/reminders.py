# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Deliver durable reminders without calling a language model."""

import json
import re
from contextlib import closing
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from .messages import PRIORITIES
from .network import api, object_map
from .store import connect, read_orders
from .time_display import local_time

if TYPE_CHECKING:
    from collections.abc import Callable


def reminder_text(title: str, deadline: str, priority: str, identifier: str) -> str:
    """Build a short HTML reminder with separate deadline and priority blocks.

    Args:
        title (str): Stored order title; escaped so user text cannot insert HTML.
        deadline (str): Aware ISO deadline, or empty when only a reminder is set.
        priority (str): Stored priority code 1, 2 or 3.
        identifier (str): Stable order identifier displayed for follow-up actions.

    Returns:
        str: Telegram HTML with meaningful bold labels and blank lines.

    Raises:
        KeyError: Priority is unknown or Moscow timezone data is unavailable.
        ValueError: A stored deadline is invalid or naive.
        OverflowError: Deadline conversion exceeds datetime bounds.
    """  # noqa: DOC502 - Date formatting and label lookups can fail.
    return "\n\n".join(
        (
            f"⏰ <b>Напоминание: {escape(title)}</b>",
            f"<b>Дедлайн</b>\n{local_time(deadline)}",
            f"Приоритет: {priority} — {PRIORITIES[priority]}",
            f"Заказ: #{escape(identifier)}",
            "Джессика ♡",
        )
    )


def deliver_due(path: Path, send: Callable[[str], None], now: datetime) -> int:
    """Send due reminders under a transaction to serialize edits and other workers.

    Commit each delivery separately. Retry failures with exponential backoff capped
    at one hour. A lost successful response can still cause a duplicate on retry;
    Telegram does not offer an idempotency key for sendMessage.

    Args:
        path (Path): Orders database filename.
        send (Callable[[str], None]): Bounded HTML sender; raises RuntimeError
            on delivery failure.
        now (datetime): Current aware timestamp used for due checks and retry times.

    Returns:
        int: Number of successfully acknowledged sends during this tick.

    Raises:
        ValueError: Now/deadline is invalid or the database schema is unsupported.
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
                text = reminder_text(
                    row["title"], row["due"], str(row["priority"]), identifier
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
        """Send one safely formatted HTML message to the configured group only.

        Args:
            text (str): HTML from reminder_text, with user fields escaped.

        Returns:
            None: Telegram acknowledged the send.

        Raises:
            RuntimeError: Transport fails or Telegram rejects the message.
        """
        try:
            result = api(
                f"https://api.telegram.org/bot{token}/sendMessage",
                {"chat_id": chat, "text": text, "parse_mode": "HTML"},
            )
            if result.get("ok") is not True:
                message = "Telegram rejected the reminder."
                raise RuntimeError(message)
        except (ValueError, TypeError, UnicodeError) as exc:
            message = "Invalid Telegram response."
            raise RuntimeError(message) from exc

    return deliver_due(Path(str(settings["database"])), send, datetime.now(UTC))
