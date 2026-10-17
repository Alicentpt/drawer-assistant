# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Persist orders and migrate shared SQLite data independently of Hermes."""

import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING, overload
from zoneinfo import ZoneInfo

from .database import Database
from .order_schema import migrate_orders
from .schema import (
    ALIBABA_SCHEMA,
    ASSETS_SCHEMA,
    BATCH_SCHEMA,
    GENERATION_SCHEMA,
    REFERENCE_FILES_SCHEMA,
    SCHEMA,
)
from .time_display import TIMEZONE

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path

    from .database import Connection, Location, Row
    from .postgres import Postgres

MAX_FIELD = 4000
ORDER_FIELDS = (
    "title",
    "client",
    "notes",
    "status",
    "due",
    "timezone",
    "remind_at",
    "brief",
    "comments",
    "priority",
)


def connect_sqlite(path: Path) -> sqlite3.Connection:
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
            if version in (0, 1, 2, 3, 4):
                migrate_orders(database)
            if version in (0, 1, 2, 3, 4, 5):
                database.execute(BATCH_SCHEMA)
                database.execute("PRAGMA user_version = 6")
            if version in (0, 1, 2, 3, 4, 5, 6):
                database.execute(GENERATION_SCHEMA)
                database.execute("PRAGMA user_version = 7")
    except sqlite3.Error, ValueError:
        database.close()
        raise
    if version not in (0, 1, 2, 3, 4, 5, 6, 7):
        database.close()
        message = "Unsupported database version; update Drawer Assistant."
        raise ValueError(message)
    return database


@overload
def connect(path: Path) -> sqlite3.Connection:  # noqa: D418 - Repo documents every function.
    """Open a local SQLite database using the shared connection entry point.

    Args:
        path (Path): Local database filename outside the source checkout.

    Returns:
        sqlite3.Connection: Initialized connection; caller closes it.

    Raises:
        OSError: The parent directory cannot be created.
        sqlite3.Error: Opening or migrating SQLite fails.
        ValueError: The database schema is unsupported.
    """  # noqa: DOC502 - Overload documents the runtime implementation.


@overload
def connect(path: Database) -> Postgres:  # noqa: D418 - Repo documents every function.
    """Open a remote PostgreSQL database using the shared connection entry point.

    Args:
        path (Database): Remote URL and independent local cache anchor.

    Returns:
        Postgres: Initialized connection; caller closes it.

    Raises:
        RuntimeError: PostgreSQL connection or migration fails.
        ValueError: The URL or schema is unsupported.
    """  # noqa: DOC502 - Overload documents the runtime implementation.


def connect(path: Location) -> Connection:
    """Open the selected database while keeping local image caches independent.

    Args:
        path (Location): SQLite filename or explicit PostgreSQL target.

    Returns:
        Connection: Initialized database connection; caller must close it.

    Raises:
        OSError: The local database directory cannot be created.
        sqlite3.Error: SQLite opening or migration fails.
        RuntimeError: PostgreSQL opening or migration fails.
        ValueError: The database URL or schema version is unsupported.
    """  # noqa: DOC502 - Backend errors propagate.
    if isinstance(path, Database):
        # Keep existing SQLite-only Hermes hosts independent of the new driver.
        from .postgres import (  # noqa: PLC0415 # pylint: disable=import-outside-toplevel
            connect_postgres,
        )

        return connect_postgres(path.url)
    return connect_sqlite(path)


@overload
@contextmanager
def transaction(path: Path) -> Generator[sqlite3.Connection]:  # noqa: D418 - Explicit docs.
    """Scope one SQLite transaction and close it on either outcome.

    Args:
        path (Path): Local database filename outside the source checkout.

    Yields:
        sqlite3.Connection: Connection committed normally or rolled back on error.

    Raises:
        OSError: The parent directory cannot be created.
        sqlite3.Error: Opening, migrating or finishing a transaction fails.
        ValueError: The database schema is unsupported.
    """  # noqa: DOC403,DOC502 - Overload documents the yielding implementation.


@overload
@contextmanager
def transaction(path: Database) -> Generator[Postgres]:  # noqa: D418 - Explicit docs.
    """Scope one PostgreSQL transaction and close it on either outcome.

    Args:
        path (Database): Remote URL and independent local cache anchor.

    Yields:
        Postgres: Connection committed normally or rolled back on error.

    Raises:
        RuntimeError: PostgreSQL operations fail.
        ValueError: The URL or schema is unsupported.
    """  # noqa: DOC403,DOC502 - Overload documents the yielding implementation.


@contextmanager
def transaction(path: Location) -> Generator[Connection]:
    """Open one transaction and close its connection on both success and failure.

    Args:
        path (Location): SQLite filename or PostgreSQL target.

    Yields:
        Connection: Connection committed on normal exit, rolled back on error.

    Raises:
        OSError: The database directory cannot be created.
        sqlite3.Error: Opening, committing or closing the database fails.
        RuntimeError: PostgreSQL operations fail.
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
        message = "Use an ISO date with the correct timezone offset, e.g. +03:00."
        raise ValueError(message)
    return moment.astimezone(UTC).isoformat(timespec="seconds")


def validate_order(values: dict[str, str]) -> dict[str, str]:
    """Validate a complete order before writing its deadline and reminder.

    Args:
        values (dict[str, str]): Full order fields; dates already normalized to UTC.

    Returns:
        dict[str, str]: The same validated fields.

    Raises:
        ValueError: Title, status, priority, field length or reminder order is invalid.
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
    if values["priority"] not in {"1", "2", "3"}:
        message = "Priority must be 1 (low), 2 (medium) or 3 (high)."
        raise ValueError(message)
    if values["due"] and values["remind_at"] > values["due"]:
        message = "Reminder must not be later than the deadline."
        raise ValueError(message)
    return values


def row_values(row: Row) -> dict[str, str]:
    """Convert a SQLite row into model-facing string fields.

    Args:
        row (Row): Row whose columns contain text or integer metadata.

    Returns:
        dict[str, str]: Column names mapped to string values.
    """
    keys = row.keys()
    return {key: str(row[key]) for key in keys}


def read_orders(database: Connection) -> list[dict[str, str]]:
    """Read orders including reminder delivery state, sorted by deadline.

    Args:
        database (Connection): Open connection using sqlite3.Row objects.

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
    values.update(status="open", timezone=TIMEZONE, priority="2")
    values.update({key: val for key, val in previous.items() if key in ORDER_FIELDS})
    values.update({key: val for key, val in params.items() if key in ORDER_FIELDS})
    for field in ("due", "remind_at"):
        if field in params:
            values[field] = utc(params[field], values["timezone"])
    if "due" in params and "remind_at" not in params:
        values["remind_at"] = values["due"]
    return validate_order(values)


def orders(path: Location, params: dict[str, str]) -> list[dict[str, str]]:
    """Create, update or list orders atomically; repeated creates are idempotent.

    Args:
        path (Location): SQLite filename or PostgreSQL target with a local cache path.
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
            "INSERT INTO orders(id,title,client,notes,status,due,timezone,remind_at,"
            "brief,comments,priority) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
            "title=excluded.title, client=excluded.client, notes=excluded.notes, "
            "status=excluded.status, due=excluded.due, timezone=excluded.timezone, "
            "remind_at=excluded.remind_at,brief=excluded.brief,"
            "comments=excluded.comments,priority=excluded.priority",
            (identifier, *(values[field] for field in ORDER_FIELDS)),
        )
        if reset:
            database.execute(
                "UPDATE orders SET sent_at='',attempts=0,next_attempt='',last_error='' "
                "WHERE id=?",
                (identifier,),
            )
        return [item for item in read_orders(database) if item["id"] == identifier]
