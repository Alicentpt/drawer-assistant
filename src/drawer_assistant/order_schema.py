# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Migrate persistent briefs, priorities, nested stages and stage image links."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

STAGE_SCHEMA = """
CREATE TABLE IF NOT EXISTS order_stages (
 order_id TEXT NOT NULL REFERENCES orders(id), id TEXT NOT NULL,
 parent_id TEXT, title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
 comments TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending',
 due TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0,
 completed_at TEXT NOT NULL DEFAULT '', PRIMARY KEY(order_id,id),
 FOREIGN KEY(order_id,parent_id) REFERENCES order_stages(order_id,id)
);
CREATE TABLE IF NOT EXISTS stage_images (
 order_id TEXT NOT NULL, stage_id TEXT NOT NULL,
 reference_id TEXT NOT NULL REFERENCES refs(id),
 PRIMARY KEY(order_id,stage_id,reference_id),
 FOREIGN KEY(order_id,stage_id) REFERENCES order_stages(order_id,id)
)
"""


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
