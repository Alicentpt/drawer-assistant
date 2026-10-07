# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Persist orders and migrate shared SQLite data independently of Hermes."""

import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

MAX_FIELD = 4000
ORDER_FIELDS = ("title", "client", "notes", "status", "due", "timezone", "remind_at")
SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, client TEXT NOT NULL,
 notes TEXT NOT NULL, status TEXT NOT NULL, due TEXT NOT NULL,
 timezone TEXT NOT NULL, remind_at TEXT NOT NULL,
 sent_at TEXT NOT NULL DEFAULT '', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt TEXT NOT NULL DEFAULT '', last_error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS refs (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL,
 order_id TEXT NOT NULL, notes TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, prompt TEXT NOT NULL, workflow TEXT NOT NULL,
 prompt_id TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'submitting',
 file TEXT NOT NULL DEFAULT '', generation_seconds TEXT NOT NULL DEFAULT ''
);
PRAGMA user_version = 2;
"""

ALIBABA_SCHEMA = """
CREATE TABLE IF NOT EXISTS alibaba_jobs (
 id TEXT PRIMARY KEY, model TEXT NOT NULL, base TEXT NOT NULL,
 fingerprint TEXT NOT NULL, request TEXT NOT NULL, task_id TEXT NOT NULL DEFAULT '',
 state TEXT NOT NULL DEFAULT 'submitting', file TEXT NOT NULL DEFAULT '',
 generation_seconds TEXT NOT NULL DEFAULT '', error_code TEXT NOT NULL DEFAULT ''
)
"""

ASSETS_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
 sha256 TEXT PRIMARY KEY, mime TEXT NOT NULL, data BLOB NOT NULL
)
"""
REFERENCE_FILES_SCHEMA = """
CREATE TABLE IF NOT EXISTS reference_files (
 reference_id TEXT PRIMARY KEY REFERENCES refs(id),
 sha256 TEXT NOT NULL REFERENCES assets(sha256), filename TEXT NOT NULL
)
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open a database and migrate orders, generation jobs and binary references.

    Args:
        path (Path): Database filename outside the source checkout.

    Returns:
        sqlite3.Connection: Open connection; the caller must close it.

    Raises:
        OSError: The parent directory cannot be created.
        sqlite3.Error: Opening or migrating the database fails.
        ValueError: A newer, unsupported schema is present.
    """  # noqa: DOC503 - Filesystem and SQLite errors propagate.
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path, timeout=35)
    database.row_factory = sqlite3.Row
    database.execute("PRAGMA foreign_keys = ON")
    try:
        with database:
            database.execute("BEGIN IMMEDIATE")
            version = database.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                database.executescript(SCHEMA)
            elif version == 1:
                database.execute(
                    "ALTER TABLE jobs ADD COLUMN generation_seconds "
                    "TEXT NOT NULL DEFAULT ''"
                )
                database.execute("PRAGMA user_version = 2")
            if version in (0, 1, 2):
                database.execute(ALIBABA_SCHEMA)
                database.execute("PRAGMA user_version = 3")
            if version in (0, 1, 2, 3):
                database.execute(ASSETS_SCHEMA)
                database.execute(REFERENCE_FILES_SCHEMA)
                database.execute("PRAGMA user_version = 4")
    except sqlite3.Error, ValueError:
        database.close()
        raise
    if version not in (0, 1, 2, 3, 4):
        database.close()
        message = "Unsupported database version; update Drawer Assistant."
        raise ValueError(message)
    return database


@contextmanager
def transaction(path: Path) -> Generator[sqlite3.Connection]:
    """Open one transaction and close its connection on both success and failure.

    Args:
        path (Path): Database filename.

    Yields:
        sqlite3.Connection: Connection committed on normal exit, rolled back on error.

    Raises:
        OSError: The database directory cannot be created.
        sqlite3.Error: Opening, committing or closing the database fails.
        ValueError: The schema is unsupported.
    """  # noqa: DOC502 - The connection implementation raises indirectly.
    database = connect(path)
    try:
        with database:
            yield database
    finally:
        database.close()


def utc(value: str, timezone: str) -> str:
    """Normalize an explicit local deadline while rejecting ambiguous input.

    Args:
        value (str): ISO 8601 timestamp with UTC offset, or empty to clear it.
        timezone (str): IANA zone matching the supplied local timestamp's offset.

    Returns:
        str: UTC ISO timestamp with seconds, or an empty string.

    Raises:
        ValueError: Timestamp lacks an offset or disagrees with the chosen zone.
        KeyError: The IANA timezone is unknown or timezone data is unavailable.
        OverflowError: Converting an extreme timestamp exceeds datetime limits.
    """  # noqa: DOC503 - ZoneInfo and datetime also raise indirectly.
    zone = ZoneInfo(timezone)
    if not value:
        return ""
    moment = datetime.fromisoformat(value)
    if (
        moment.tzinfo is None
        or moment.utcoffset() != moment.astimezone(zone).utcoffset()
    ):
        message = "Use an ISO date with the correct timezone offset, e.g. +02:00."
        raise ValueError(message)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def validate_order(values: dict[str, str]) -> dict[str, str]:
    """Validate a complete order before writing its deadline and reminder.

    Args:
        values (dict[str, str]): Full order fields; dates already normalized to UTC.

    Returns:
        dict[str, str]: The same validated fields.

    Raises:
        ValueError: Title, status, field length or reminder ordering is invalid.
        KeyError: A required field or timezone is unknown.
    """  # noqa: DOC503 - Mapping and ZoneInfo failures propagate.
    ZoneInfo(values["timezone"])
    if not values["title"].strip() or any(
        len(value) > MAX_FIELD for value in values.values()
    ):
        message = "Title is required; each field must contain at most 4000 characters."
        raise ValueError(message)
    if values["status"] not in {"open", "done", "cancelled"}:
        message = "Status must be open, done or cancelled."
        raise ValueError(message)
    if values["due"] and values["remind_at"] > values["due"]:
        message = "Reminder must not be later than the deadline."
        raise ValueError(message)
    return values


def row_values(row: sqlite3.Row) -> dict[str, str]:
    """Convert a SQLite row into model-facing string fields.

    Args:
        row (sqlite3.Row): Row whose columns contain text or integer metadata.

    Returns:
        dict[str, str]: Column names mapped to string values.
    """
    keys = row.keys()
    return {key: str(row[key]) for key in keys}


def read_orders(database: sqlite3.Connection) -> list[dict[str, str]]:
    """Read orders including reminder delivery state, sorted by deadline.

    Args:
        database (sqlite3.Connection): Open connection using sqlite3.Row objects.

    Returns:
        list[dict[str, str]]: Persisted fields; numeric delivery attempts as text.

    Raises:
        sqlite3.Error: The query fails.
    """  # noqa: DOC502 - SQLite raises indirectly.
    return [
        row_values(row)
        for row in database.execute("SELECT * FROM orders ORDER BY due, id")
    ]


def merge_order(params: dict[str, str], previous: dict[str, str]) -> dict[str, str]:
    """Merge changed fields, normalizing dates without reinterpreting stored UTC.

    Args:
        params (dict[str, str]): New field values from a create or update call.
        previous (dict[str, str]): Existing fields, or empty for a new order.

    Returns:
        dict[str, str]: Complete validated order fields.

    Raises:
        ValueError: Fields or dates are invalid.
        KeyError: Timezone is unknown.
        OverflowError: Converting a timestamp exceeds datetime limits.
    """  # noqa: DOC502 - Validation errors propagate.
    values: dict[str, str] = dict.fromkeys(ORDER_FIELDS, "")
    values.update(status="open", timezone="Europe/Kaliningrad")
    values.update({key: val for key, val in previous.items() if key in ORDER_FIELDS})
    values.update({key: val for key, val in params.items() if key in ORDER_FIELDS})
    for field in ("due", "remind_at"):
        if field in params:
            values[field] = utc(params[field], values["timezone"])
    if "due" in params and "remind_at" not in params:
        values["remind_at"] = values["due"]
    return validate_order(values)


def orders(path: Path, params: dict[str, str]) -> list[dict[str, str]]:
    """Create, update or list orders atomically; repeated creates are idempotent.

    Args:
        path (Path): SQLite database filename.
        params (dict[str, str]): Action, stable id and optional ORDER_FIELDS. Empty
            dates clear them; a changed due date defaults its reminder to that date.

    Returns:
        list[dict[str, str]]: All orders for list, or the saved order for mutations.

    Raises:
        ValueError: Action, identifier, fields, dates or schema version are invalid.
        KeyError: A required argument, order or timezone is missing.
        OSError: The database directory cannot be created.
        sqlite3.Error: Reading or committing the transaction fails.
        OverflowError: A timestamp conversion exceeds datetime limits.
    """  # noqa: DOC503 - Includes delegated validation and storage errors.
    action = params["action"]
    if action not in {"list", "create", "update"}:
        message = "Orders action must be list, create or update."
        raise ValueError(message)
    with transaction(path) as database:
        database.execute("BEGIN IMMEDIATE")
        existing = read_orders(database)
        if action == "list":
            return existing
        identifier = params["id"]
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier):
            message = "Use a stable order id of 1-64 letters, digits, _ or -."
            raise ValueError(message)
        previous = next((item for item in existing if item["id"] == identifier), None)
        if action == "update" and previous is None:
            raise KeyError(identifier)
        values = merge_order(params, previous or {})
        if action == "create" and previous:
            if any(previous[field] != values[field] for field in ORDER_FIELDS):
                message = "Order id already exists; use update or a different id."
                raise ValueError(message)
            return [previous]
        reset = previous is None or any(
            previous[field] != values[field] for field in ("due", "remind_at", "status")
        )
        database.execute(
            "INSERT INTO orders(id,title,client,notes,status,due,timezone,remind_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "title=excluded.title, client=excluded.client, notes=excluded.notes, "
            "status=excluded.status, due=excluded.due, timezone=excluded.timezone, "
            "remind_at=excluded.remind_at",
            (identifier, *(values[field] for field in ORDER_FIELDS)),
        )
        if reset:
            database.execute(
                "UPDATE orders SET sent_at='',attempts=0,next_attempt='',last_error='' "
                "WHERE id=?",
                (identifier,),
            )
        return [item for item in read_orders(database) if item["id"] == identifier]
