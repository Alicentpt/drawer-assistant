# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Persist chat provenance independently of immutable GPU submission intents."""

import json
import re
from time import time
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from drawer_assistant.database import configured_database
from drawer_assistant.network import object_map
from drawer_assistant.store import transaction

from .presets import canonical

if TYPE_CHECKING:
    from pathlib import Path

    from drawer_assistant.database import Location

START_TOOLS = frozenset(
    "mcp__drawer_generation__" + name
    for name in ("anima_generate", "anima_style", "anima_depth", "anima_batch")
)


def capture(
    settings_file: Path,
    tool_name: str,
    arguments: dict[str, object],
    origin: dict[str, str],
) -> None:
    """Bind future output to the host-supplied, allowlisted Telegram origin.

    This runs before submission so a gateway interruption cannot lose routing.
    Local CLI/MCP calls never silently acquire a Telegram destination. A stale
    route without a corresponding task is harmless; the worker skips it.

    Args:
        settings_file (Path): Named profile's plugin settings.
        tool_name (str): Host-observed MCP tool name, including its namespace.
        arguments (dict[str, object]): Model arguments, used only for request_id.
        origin (dict[str, str]): Trusted platform/chat/thread/user from Hermes.

    Returns:
        None: A route is stored once, or this non-generation/local call is ignored.

    Raises:
        ValueError: Configuration, id, group or an existing route is incompatible.
        OSError: Profile files or SQLite cannot be accessed.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: SQLite operations fail.
        KeyError: Required configuration fields are absent.
        TypeError: Configuration cannot be serialized.
    """  # noqa: DOC503 - Configuration and storage errors propagate.
    if tool_name not in START_TOOLS:
        return
    settings = object_map(json.loads(settings_file.read_text("utf-8")))
    if settings.get("generation_auto_delivery") is not True:
        return
    if origin.get("platform") != "telegram":
        return
    allowed = dotenv_values(
        settings_file.parents[2] / ".env", interpolate=False, encoding="utf-8-sig"
    ).get("TELEGRAM_ALLOWED_CHATS", "")
    identifier = str(arguments.get("request_id", ""))
    if (
        not re.fullmatch(r"-[1-9]\d*", allowed or "")
        or origin.get("chat_id") != allowed
        or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier)
    ):
        message = "Automatic generation delivery requires the configured group and id."
        raise ValueError(message)
    path = configured_database(settings_file, settings)
    with transaction(path) as database:
        database.execute("BEGIN IMMEDIATE")
        existing = database.execute(
            "SELECT origin FROM generation_deliveries WHERE id=?", (identifier,)
        ).fetchone()
        if existing is not None:
            saved = object_map(json.loads(existing["origin"]))
            if any(
                saved.get(key, "") != origin.get(key, "")
                for key in ("chat_id", "thread_id")
            ):
                message = "Task already belongs to a different conversation."
                raise ValueError(message)
            return
        # Historical jobs predate provenance and may already have been delivered.
        if database.execute(
            "SELECT id FROM generation_tasks WHERE id=?", (identifier,)
        ).fetchone():
            return
        database.execute(
            "INSERT INTO generation_deliveries(id,origin,created) VALUES(?,?,?)",
            (identifier, canonical(origin), time()),
        )


def information(path: Location, identifier: str) -> dict[str, object]:
    """Expose delivery state without exposing chat identifiers to the model.

    Args:
        path (Location): Shared task and routing database.
        identifier (str): Existing or prospective generation id.

    Returns:
        dict[str, object]: Explicit automatic flag, disposition and last error code.

    Raises:
        OSError: SQLite cannot be accessed.
        ValueError: The database schema is unsupported.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC502 - Storage errors propagate.
    with transaction(path) as database:
        row = database.execute(
            "SELECT state,last_error FROM generation_deliveries WHERE id=?",
            (identifier,),
        ).fetchone()
    if row is None:
        return {"automatic": False}
    return {"automatic": True, "state": row["state"], "last_error": row["last_error"]}
