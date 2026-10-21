# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Advance routed GPU tasks and hand results to Hermes without an LLM turn."""

import argparse
import json
import logging
import os
import signal
import sqlite3
from pathlib import Path
from threading import Event
from time import time
from typing import TYPE_CHECKING
from uuid import uuid4

from drawer_assistant.database import configured_database
from drawer_assistant.network import object_map
from drawer_assistant.store import row_values, transaction
from drawer_assistant.telegram_access import recipient

from . import batches, jobs
from .hermes_delivery import Queue, queue
from .presets import array

if TYPE_CHECKING:
    from types import FrameType

    from drawer_assistant.database import Location

LOGGER = logging.getLogger(__name__)
UNCERTAIN_AFTER = 600


def content(result: dict[str, object]) -> str:
    """Format final output and exact depth previews as native MEDIA attachments.

    Args:
        result (dict[str, object]): Sanitized persisted single/batch result.

    Returns:
        str: Brief Russian message with timings, stable id and available media.

    Raises:
        ValueError: Frame metadata is malformed.
        KeyError: Required result fields are missing.
    """  # noqa: DOC502 - Mapping validation errors propagate.
    frames = [object_map(item) for item in array(result.get("frames", []))]
    rows = frames or [result]
    success = result["state"] == "completed"
    text = [
        "Готово ♡"
        if success
        else "Генерации нужна проверка — автоматически не повторяю."
    ]
    text.append("Задание: #" + str(result["request_id"]))
    timings = [
        str(row["generation_seconds"]) + " сек."
        for row in rows
        if row.get("generation_seconds")
    ]
    if timings:
        text.append("Генерация: " + " + ".join(timings))
    if result.get("assembly_seconds") is not None:
        text.append("Сборка: " + str(result["assembly_seconds"]) + " сек.")
    files = (
        [str(result["file"])]
        if result.get("file")
        else [str(row["file"]) for row in rows if row.get("file")]
    )
    text.extend("MEDIA:" + file for file in files)
    for index, row in enumerate(rows, 1):
        if row.get("depth_preview"):
            window = object_map(row["depth_window"])
            text.extend(
                (
                    (
                        f"Карта глубины {index}: окно "
                        f"{window['start_percent']} - {window['end_percent']}"
                    ),
                    "MEDIA:" + str(row["depth_preview"]),
                )
            )
    return "\n\n".join(text)


def claim(path: Location, owner: str, now: float) -> dict[str, str] | None:
    """Lease one eligible routed task without keeping a transaction over network IO.

    Args:
        path (Location): Shared task database.
        owner (str): Unique worker-tick lease owner.
        now (float): Unix seconds used for the bounded ten-minute recovery lease.

    Returns:
        dict[str, str] | None: Claimed route, or None if no work is eligible.

    Raises:
        OSError: SQLite cannot be accessed.
        ValueError: The schema is unsupported.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC502 - Database failures propagate.
    with transaction(path) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT * FROM generation_deliveries WHERE state IN ('waiting','queued') "
            "AND next_attempt<=? ORDER BY next_attempt,created,id LIMIT 1",
            (now,),
        ).fetchone()
        if row is None:
            return None
        database.execute(
            "UPDATE generation_deliveries SET owner=?,next_attempt=? WHERE id=?",
            (owner, now + UNCERTAIN_AFTER, row["id"]),
        )
        return {**row_values(row), "owner": owner}


def advance(
    path: Location, route: dict[str, str], executable: str, now: float
) -> dict[str, object] | None:
    """Advance one task once; never submit an uncertain GPU request again.

    Args:
        path (Location): Task database and persistent image cache anchor.
        route (dict[str, str]): Captured route with task id and creation timestamp.
        executable (str): Configured ImageMagick executable or empty for PATH.
        now (float): Unix clock; protects submissions still actively in progress.

    Returns:
        dict[str, object] | None: Terminal output, or None while absent/in progress.

    Raises:
        OSError: Files cannot be accessed.
        ValueError: Persisted spec or server result is invalid.
        RuntimeError: ComfyUI, ImageMagick or PostgreSQL fails.
        sqlite3.Error: SQLite operations fail.
        KeyError: Required stored/server fields are absent.
        TypeError: Result cannot be serialized.
    """  # noqa: DOC502 - Generation/storage errors propagate.
    try:
        row = jobs.read(path, route["id"])
    except KeyError:
        if now - float(route["created"]) >= UNCERTAIN_AFTER:
            return {"request_id": route["id"], "state": "not_submitted"}
        return None
    if row["state"] == "submitting" and now - float(route["created"]) < UNCERTAIN_AFTER:
        return None
    spec = object_map(json.loads(row["spec"]))
    result = (
        batches.advance(path, route["id"], executable)
        if spec["mode"] == "batch"
        else jobs.poll(path, route["id"])
    )
    if result["state"] in {"completed", "failed", "needs_attention", "submitting"}:
        return result
    return None


def tick(settings_file: Path, native_queue: Queue, now: float) -> bool:
    """Perform a bounded leased step and idempotent native delivery handoff.

    Failed polling backs off up to a minute. A gateway-deferred send remains in
    its native queue across restarts. Unknown/failed native sends are terminal:
    Telegram has no idempotency key, so an uncertain image is never replayed.

    Args:
        settings_file (Path): Named profile's plugin configuration.
        native_queue (Queue): Hermes queue or an isolated test substitute.
        now (float): Unix timestamp for this tick.

    Returns:
        bool: True if an eligible route was inspected, False if idle/disabled.

    Raises:
        OSError: Configuration or storage cannot be accessed.
        ValueError: Configuration or schema is invalid.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: SQLite operations fail.
        KeyError: Required configuration is absent.
    """  # noqa: DOC502 - Initial configuration/storage failures propagate.
    settings = object_map(json.loads(settings_file.read_text("utf-8")))
    if settings.get("generation_auto_delivery") is not True:
        return False
    path = configured_database(settings_file, settings)
    route = claim(path, uuid4().hex, now)
    if route is None:
        return False
    state, error, delay = route["state"], "", 5
    try:
        origin = object_map(json.loads(route["origin"]))
        allowed = recipient(settings_file.parents[2])
        if (
            origin.get("platform") != "telegram"
            or origin.get("chat_id") != allowed
            or (not allowed.startswith("-") and origin.get("user_id") != allowed)
        ):
            state, error = "blocked", "destination_changed"
        else:
            result = advance(
                path, route, str(settings.get("imagemagick_binary") or ""), now
            )
            if result is not None:
                if result["state"] == "not_submitted":
                    state = "cancelled"
                else:
                    receipt = native_queue.enqueue(
                        "drawer-generation:" + route["id"],
                        {
                            "id": "drawer-generation:" + route["id"],
                            "name": "Генерация",
                            "origin": origin,
                            "deliver": "origin",
                            "attach_to_session": True,
                        },
                        content(result),
                    )
                    state = str(receipt["status"])
                    if state in {"pending", "delivering"}:
                        state = "queued"
    except (
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.Error,
        KeyError,
        TypeError,
    ) as exc:
        error = type(exc).__name__
        delay = min(60, 5 * 2 ** min(int(route["attempts"]), 4))
    with transaction(path) as database:
        database.execute(
            "UPDATE generation_deliveries SET state=?,last_error=?,next_attempt=?,"
            "attempts=attempts+1,owner='' WHERE id=? AND owner=?",
            (state, error, now + delay, route["id"], route["owner"]),
        )
    return True


def main() -> None:
    """Run the supervised worker; use the named profile for native queue ownership.

    Returns:
        None: Stops on SIGTERM/SIGINT; durable tasks resume after restart.

    Raises:
        SystemExit: Required command-line settings path is missing.
        ImportError: Native Hermes delivery API is unavailable.
        OSError: Configuration/heartbeat files cannot be accessed.
    """  # noqa: DOC502 - CLI/import and file errors propagate.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    settings_file = parser.parse_args().settings.resolve()
    os.environ["HERMES_HOME"] = str(settings_file.parents[2])
    native_queue = queue()
    stopped = Event()

    def stop(number: int, frame: FrameType | None) -> None:
        """Request graceful shutdown after the current bounded network operation.

        Args:
            number (int): Unused signal number.
            frame (FrameType | None): Unused interrupted stack frame.

        Returns:
            None: The next wait wakes immediately.
        """
        del number, frame
        stopped.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    heartbeat = settings_file.parent / "generation-worker.json"
    while not stopped.is_set():
        try:
            busy = tick(settings_file, native_queue, time())
            heartbeat.write_text(
                json.dumps({"checked_at": time(), "ok": True}), "utf-8"
            )
        except (OSError, ValueError, RuntimeError, sqlite3.Error, KeyError) as exc:
            # Driver tracebacks may contain credentials or private request data.
            LOGGER.error("Generation worker tick failed: %s", type(exc).__name__)  # noqa: TRY400
            busy = False
        stopped.wait(1 if busy else 5)


if __name__ == "__main__":
    main()
