# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Persist nested order stages and links to durably stored reference images."""

import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .references import METADATA
from .store import MAX_FIELD, row_values, transaction, utc

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

FIELDS = ("parent_id", "title", "description", "comments", "status", "due", "position")
STATUSES = {"pending", "in_progress", "blocked", "done", "cancelled"}
MAX_DEPTH = 16
MAX_POSITION_DIGITS = 6


def stage_rows(database: sqlite3.Connection, order_id: str) -> list[dict[str, str]]:
    """Read ordered stage fields without image bytes or recursive expansion.

    Args:
        database (sqlite3.Connection): Open connection with Row objects.
        order_id (str): Parent order identifier.

    Returns:
        list[dict[str, str]]: Flat stage metadata, numeric fields rendered as text.

    Raises:
        sqlite3.Error: Reading stage metadata fails.
    """  # noqa: DOC502 - SQLite raises indirectly.
    return [
        {**row_values(row), "parent_id": row["parent_id"] or ""}
        for row in database.execute(
            "SELECT * FROM order_stages WHERE order_id=? ORDER BY position,id",
            (order_id,),
        )
    ]


def validate_tree(rows: list[dict[str, str]]) -> None:
    """Reject missing parents, cycles and trees deeper than sixteen stages.

    Args:
        rows (list[dict[str, str]]): Proposed complete stage tree for one order.

    Returns:
        None: Every stage has a valid ancestor chain.

    Raises:
        KeyError: A required field or proposed parent is absent.
        ValueError: A cycle or excessive nesting depth is found.
    """  # noqa: DOC503 - Mapping lookups can also raise.
    indexed = {row["id"]: row for row in rows}
    for row in rows:
        seen: set[str] = set()
        current = row["id"]
        while current:
            if current in seen or len(seen) >= MAX_DEPTH:
                message = "Stage parents must be acyclic, at most 16 levels deep."
                raise ValueError(message)
            seen.add(current)
            current = indexed[current]["parent_id"]


def stage_tree(
    database: sqlite3.Connection, order_id: str, parent_id: str = ""
) -> list[dict[str, object]]:
    """Expand one order's stages with children and image metadata.

    Args:
        database (sqlite3.Connection): Open transaction for a consistent snapshot.
        order_id (str): Parent order identifier.
        parent_id (str, default=""): Parent stage; empty selects top-level stages.

    Returns:
        list[dict[str, object]]: Stage fields, nested children and images; no BLOBs.

    Raises:
        sqlite3.Error: Stage or reference metadata cannot be read.
        KeyError: Persisted stage metadata lacks a required column or parent.
        ValueError: Persisted parents form a cycle or exceed the depth limit.
    """  # noqa: DOC502 - Recursive reads and mappings can fail.
    result: list[dict[str, object]] = []
    rows = stage_rows(database, order_id)
    if not parent_id:
        validate_tree(rows)
    for row in rows:
        if row["parent_id"] != parent_id:
            continue
        images = [
            row_values(image)
            for image in database.execute(
                METADATA + " JOIN stage_images ON refs.id=stage_images.reference_id "
                "WHERE stage_images.order_id=? AND stage_id=? AND refs.order_id=?",
                (order_id, row["id"], order_id),
            )
        ]
        result.append(
            {
                **row,
                "children": stage_tree(database, order_id, row["id"]),
                "images": images,
            }
        )
    return result


def write_stage(database: sqlite3.Connection, params: dict[str, str]) -> None:
    """Create or patch a stage while retaining siblings, results and completion time.

    Args:
        database (sqlite3.Connection): Existing writable transaction.
        params (dict[str, str]): create/update, order_id/id and changed stage fields.

    Returns:
        None: The valid stage is upserted; the caller owns the transaction.

    Raises:
        ValueError: Fields, status, identifier, dates, duplicate id or tree are invalid.
        KeyError: Order, stage, parent or a required argument is missing.
        sqlite3.Error: Reading or writing stage data fails.
        OverflowError: Date conversion exceeds datetime bounds.
    """  # noqa: DOC503 - Validation, SQL and mapping errors propagate.
    order_id, identifier = params["order_id"], params["id"]
    order = database.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
    if order is None:
        raise KeyError(order_id)
    rows = stage_rows(database, order_id)
    previous = next((row for row in rows if row["id"] == identifier), None)
    if params["action"] == "update" and previous is None:
        raise KeyError(identifier)
    values: dict[str, str] = dict.fromkeys(FIELDS, "")
    values.update(status="pending", position="0")
    values.update({key: val for key, val in (previous or {}).items() if key in FIELDS})
    values.update({key: params[key] for key in FIELDS if key in params})
    if "due" in params:
        values["due"] = utc(params["due"], order["timezone"])
    invalid = (
        not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier),
        not values["title"].strip(),
        values["status"] not in STATUSES,
        not values["position"].isdigit(),
        len(values["position"]) > MAX_POSITION_DIGITS,
        any(len(value) > MAX_FIELD for value in values.values()),
    )
    if any(invalid):
        message = "Use a stage id/title, valid status and position 0..999999."
        raise ValueError(message)
    if params["action"] == "create" and previous:
        if any(previous[field] != values[field] for field in FIELDS):
            message = "Stage id already exists; use update or another id."
            raise ValueError(message)
        return
    validate_tree(
        [row for row in rows if row["id"] != identifier]
        + [
            {"id": identifier, **values},
        ]
    )
    completed = (previous or {}).get("completed_at", "")
    if values["status"] == "done":
        completed = completed or datetime.now(UTC).isoformat(timespec="seconds")
    else:
        completed = ""
    database.execute(
        "INSERT INTO order_stages(order_id,id,parent_id,title,description,comments,"
        "status,due,position,completed_at) VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(order_id,id) DO UPDATE SET parent_id=excluded.parent_id,"
        "title=excluded.title,description=excluded.description,"
        "comments=excluded.comments,"
        "status=excluded.status,due=excluded.due,position=excluded.position,"
        "completed_at=excluded.completed_at",
        (
            order_id,
            identifier,
            values["parent_id"] or None,
            *(values[field] for field in FIELDS if field != "parent_id"),
            completed,
        ),
    )


def stages(path: Path, params: dict[str, str]) -> list[dict[str, object]]:
    """Manage nested stages or attach an already saved binary reference to a stage.

    Args:
        path (Path): Orders and reference database filename.
        params (dict[str, str]): action create/update/list/attach, order_id, stage id;
            attach additionally requires reference_id belonging to that order.

    Returns:
        list[dict[str, object]]: Entire nested stage tree after the operation.

    Raises:
        ValueError: Action, stage fields, image storage or tree is invalid.
        KeyError: Required arguments, order, stage, reference or parent is missing.
        OSError: Database directory cannot be accessed.
        sqlite3.Error: Reading or committing the operation fails.
        OverflowError: Timestamp conversion exceeds datetime bounds.
    """  # noqa: DOC503 - Storage and validation errors propagate.
    action, order_id = params["action"], params["order_id"]
    if action not in {"create", "update", "list", "attach"}:
        message = "Use create, update, list or attach for stages."
        raise ValueError(message)
    with transaction(path) as database:
        database.execute("BEGIN IMMEDIATE")
        if not database.execute(
            "SELECT 1 FROM orders WHERE id=?", (order_id,)
        ).fetchone():
            raise KeyError(order_id)
        if action in {"create", "update"}:
            write_stage(database, params)
        if action == "attach":
            image = database.execute(
                METADATA + " WHERE refs.id=? AND refs.order_id=?",
                (params["reference_id"], order_id),
            ).fetchone()
            if image is None or image["storage"] != "blob":
                message = "Save the actual image to this order before attaching it."
                raise ValueError(message)
            database.execute(
                "INSERT INTO stage_images VALUES(?,?,?) ON CONFLICT DO NOTHING",
                (order_id, params["id"], params["reference_id"]),
            )
        return stage_tree(database, order_id)
