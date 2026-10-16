# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Migrate persistent briefs, priorities, nested stages and stage image links."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

from .schema import STAGE_SCHEMA


def migrate_orders(database: sqlite3.Connection) -> None:
    """Add order structure without changing stored deadline or reminder instants.

    Args:
        database (sqlite3.Connection): Writable connection inside the migration.

    Returns:
        None: Missing columns/tables are added and existing zones become Moscow.

    Raises:
        sqlite3.Error: Migration statements cannot be applied.
    """  # noqa: DOC502 - SQLite errors propagate.
    columns = {row[1] for row in database.execute("PRAGMA table_info(orders)")}
    # Fixed declarations, never user input; older test/imported schemas may be partial.
    for field, declaration in (
        ("brief", "TEXT NOT NULL DEFAULT ''"),
        ("comments", "TEXT NOT NULL DEFAULT ''"),
        ("priority", "INTEGER NOT NULL DEFAULT 2 CHECK(priority BETWEEN 1 AND 3)"),
    ):
        if field not in columns:
            database.execute(f"ALTER TABLE orders ADD COLUMN {field} {declaration}")
    for statement in STAGE_SCHEMA.split(";"):
        database.execute(statement)
    database.execute("UPDATE orders SET timezone='Europe/Moscow'")
    database.execute("PRAGMA user_version = 5")
