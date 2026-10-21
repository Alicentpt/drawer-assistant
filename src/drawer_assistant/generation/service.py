# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Dispatch validated generation tools without depending on an agent runtime."""

import json
from contextlib import suppress
from pathlib import Path
from time import monotonic, sleep, time
from typing import TYPE_CHECKING

from jsonschema import Draft202012Validator

from drawer_assistant.database import configured_database
from drawer_assistant.image_sheet import sheet
from drawer_assistant.network import object_map

from . import batches, jobs
from .delivery import information
from .health import inspect_server
from .presets import normalize

HEARTBEAT_MAX_AGE = 120

if TYPE_CHECKING:
    from drawer_assistant.database import Location


def schemas() -> dict[str, object]:
    """Load the single source of MCP tool descriptions and JSON schemas.

    Returns:
        dict[str, object]: Name-to-definition mapping.

    Raises:
        OSError: Packaged definitions cannot be read.
        ValueError: Definitions are invalid JSON objects.
    """  # noqa: DOC502 - Filesystem and parser failures propagate.
    return object_map(
        json.loads((Path(__file__).parent / "tools.json").read_text("utf-8"))
    )


def worker_running(settings_file: Path) -> bool:
    """Check recent worker progress without exposing local paths or credentials.

    Args:
        settings_file (Path): Profile-local plugin settings next to the heartbeat.

    Returns:
        bool: True for a readable heartbeat no older than two minutes.
    """
    heartbeat = settings_file.parent / "generation-worker.json"
    with suppress(OSError, ValueError, KeyError):
        stamp = object_map(json.loads(heartbeat.read_text("utf-8")))["checked_at"]
        return 0 <= time() - float(str(stamp)) < HEARTBEAT_MAX_AGE
    return False


def status(path: Location, identifier: str, executable: str) -> dict[str, object]:
    """Wait with bounded polling without holding database connections open.

    Args:
        path (Location): Shared database and local cache anchor.
        identifier (str): Single or batch request id.
        executable (str): Configured ImageMagick executable.

    Returns:
        dict[str, object]: Latest result after completion, failure or 25 seconds.

    Raises:
        ValueError: Saved spec, image or graph is invalid.
        KeyError: Task or expected output is absent.
        OSError: Files cannot be read or written.
        RuntimeError: Network, PostgreSQL or ImageMagick fails.
        TypeError: Saved data cannot be serialized.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC502 - Underlying generation and storage failures propagate.
    deadline = monotonic() + 25
    batch = (
        object_map(json.loads(jobs.read(path, identifier)["spec"]))["mode"] == "batch"
    )
    while True:
        result = (
            batches.advance(path, identifier, executable)
            if batch
            else jobs.poll(path, identifier)
        )
        remaining = deadline - monotonic()
        if (
            result["state"] in {"completed", "failed", "submitting", "needs_attention"}
            or remaining <= 0
        ):
            return result
        sleep(min(5, remaining))


def invoke(
    settings_file: Path, tool: str, params: dict[str, object]
) -> dict[str, object]:
    """Validate a tool call and use only the operator's configured endpoint.

    Args:
        settings_file (Path): Profile-local plugin settings; database credentials
            are read from that profile's .env, never from tool arguments.
        tool (str): Allowlisted MCP tool name.
        params (dict[str, object]): Model-supplied JSON arguments.

    Returns:
        dict[str, object]: Credential-free generation, health or assembly result.

    Raises:
        ValueError: Arguments, configuration, graph or persisted data is invalid.
        KeyError: Unknown tool/task or required data is absent.
        OSError: Configuration, input or output cannot be accessed.
        RuntimeError: ComfyUI, PostgreSQL or ImageMagick fails.
        TypeError: Data cannot be serialized.
        sqlite3.Error: SQLite operations fail.
        jsonschema.ValidationError: Tool arguments do not match their schema.
    """  # noqa: DOC502 - Delegated validation, storage and transport errors propagate.
    definition = object_map(schemas()[tool])
    # types-jsonschema leaves validate variadic/untyped; the runtime validator
    # checks every nested value against our strict JSON schemas before dispatch.
    Draft202012Validator(object_map(definition["inputSchema"])).validate(params)  # pyright: ignore[reportUnknownMemberType]
    settings = object_map(json.loads(settings_file.read_text("utf-8")))
    path = configured_database(settings_file, settings)
    base = str(settings.get("comfy_anima_url") or settings["comfy_url"]).rstrip("/")
    executable = str(settings.get("imagemagick_binary") or "")
    if tool == "generation_health":
        return {
            **inspect_server(base),
            "automatic_delivery": settings.get("generation_auto_delivery") is True,
            "worker_running": worker_running(settings_file),
        }
    if tool == "image_sheet":
        return sheet(path.parent / "image-sheets", params, executable)
    identifier = str(params["request_id"])
    delivery = information(path, identifier)
    if tool == "generation_status":
        result = (
            jobs.public(jobs.read(path, identifier))
            if delivery["automatic"]
            else status(path, identifier, executable)
        )
    elif tool == "anima_batch":
        spec = batches.prepare(path, params)
        created = jobs.reserve(path, base, identifier, spec)
        result = (
            batches.advance(path, identifier, executable)
            if created or not delivery["automatic"]
            else jobs.public(jobs.read(path, identifier))
        )
    else:
        mode = "text" if tool == "anima_generate" else tool.removeprefix("anima_")
        spec = normalize(mode, params, path.parent / "generation-inputs")
        result = jobs.submit(path, base, identifier, spec)
    return {**result, "delivery": information(path, identifier)}
