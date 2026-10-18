# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Persist immutable generation intents before any uncertain remote submission."""

import hashlib
import json
import re
from typing import TYPE_CHECKING

from drawer_assistant.comfy import execution_seconds
from drawer_assistant.network import api, object_map
from drawer_assistant.store import row_values, transaction

from .graphs import build
from .health import inspect_server
from .presets import CONTROLS, GenerationRequestError, array, canonical
from .transport import download, upload

if TYPE_CHECKING:
    from drawer_assistant.database import Location


def read(path: Location, identifier: str) -> dict[str, str]:
    """Read persisted task state without reaching ComfyUI.

    Args:
        path (Location): Shared database and independent local cache anchor.
        identifier (str): Stable task identifier.

    Returns:
        dict[str, str]: Private storage record, including serialized request/result.

    Raises:
        KeyError: Task does not exist.
        OSError: SQLite files cannot be accessed.
        ValueError: Database schema is unsupported.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC503 - Database errors propagate.
    with transaction(path) as database:
        row = database.execute(
            "SELECT * FROM generation_tasks WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        return row_values(row)


def reserve(
    path: Location, base: str, identifier: str, spec: dict[str, object]
) -> bool:
    """Atomically claim an immutable task before a remote mutation.

    Args:
        path (Location): Shared application database.
        base (str): Configured endpoint saved with the task for future polling.
        identifier (str): Stable letters/digits/dash/underscore id, at most 64.
        spec (dict[str, object]): Normalized single or batch request.

    Returns:
        bool: True only for the caller that created the task.

    Raises:
        ValueError: Identifier or retry parameters differ from the original.
        TypeError: Specification contains non-JSON values.
        OSError: SQLite files cannot be accessed.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC503 - Storage and serialization failures propagate.
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier):
        message = "Use a stable 1..64 character task id."
        raise ValueError(message)
    serialized = canonical(spec)
    with transaction(path) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT spec,base FROM generation_tasks WHERE id=?", (identifier,)
        ).fetchone()
        if row:
            if row["spec"] != serialized or row["base"] != base:
                message = "Task id already exists with different inputs or endpoint."
                raise ValueError(message)
            return False
        database.execute(
            "INSERT INTO generation_tasks(id,spec,base) VALUES(?,?,?)",
            (identifier, serialized, base),
        )
    return True


def update(path: Location, identifier: str, values: dict[str, str]) -> None:
    """Persist acknowledged state using a fixed column allowlist.

    Args:
        path (Location): Application database.
        identifier (str): Existing task id.
        values (dict[str, str]): state, prompt_id and/or serialized result.

    Returns:
        None: Supplied fields are committed atomically.

    Raises:
        ValueError: Unknown columns are supplied.
        OSError: SQLite files cannot be accessed.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC503 - Database errors propagate.
    if not values or values.keys() - {"state", "prompt_id", "result"}:
        message = "Invalid task update fields."
        raise ValueError(message)
    fields = ",".join(key + "=?" for key in values)
    with transaction(path) as database:
        database.execute(
            # fields contains only the fixed allowlist checked above.
            "UPDATE generation_tasks SET " + fields + " WHERE id=?",  # noqa: S608 # nosec B608
            (*values.values(), identifier),
        )


def submit(
    path: Location, base: str, identifier: str, spec: dict[str, object]
) -> dict[str, object]:
    """Check readiness, reserve once, upload references and submit a fixed graph.

    Args:
        path (Location): Application database and image cache anchor.
        base (str): Configured ComfyUI URL.
        identifier (str): Stable unique request id.
        spec (dict[str, object]): Validated normalized single-frame specification.

    Returns:
        dict[str, object]: Queued or existing task metadata, never duplicate work.

    Raises:
        ValueError: Inputs or workflow are invalid, or retry inputs differ.
        KeyError: Required data or acknowledgement is missing.
        OSError: Image or workflow files cannot be read.
        RuntimeError: Server is unavailable/not ready or database operation fails.
        GenerationRequestError: A removed mode attempts to submit new work.
        TypeError: Graph cannot be serialized.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC503 - Delegated transport, storage and graph failures propagate.
    # Repeated starts return saved state even while the artist PC is offline.
    try:
        existing = read(path, identifier)
    except KeyError:
        existing = None
    if existing is not None:
        reserve(path, base, identifier, spec)
        return public(existing)
    if spec["mode"] not in CONTROLS:
        message = "This generation mode was removed; select text, style or depth."
        raise GenerationRequestError(message)
    readiness = object_map(object_map(inspect_server(base)["modes"])[str(spec["mode"])])
    if not readiness["ready"]:
        message = (
            "ComfyUI mode is not ready; call generation_health "
            "for missing dependencies."
        )
        raise RuntimeError(message)
    if not reserve(path, base, identifier, spec):
        return public(read(path, identifier))
    refs = array(spec["references"])
    names = [upload(base, str(file)) for file in refs]
    options = object_map(spec["options"])
    seed = int(
        str(
            options.get(
                "seed", int(hashlib.sha256(identifier.encode()).hexdigest()[:12], 16)
            )
        )
    )
    graph = build(spec, names, seed)
    metadata: dict[str, object] = {
        "mode": spec["mode"],
        "seed": seed,
        "options": options,
        "positive_prompt": object_map(object_map(graph["4"])["inputs"])["text"],
        "negative_prompt": options["negative_prompt"],
    }
    update(path, identifier, {"result": canonical(metadata)})
    # A failed/uncertain POST intentionally leaves submitting; never auto-resubmit.
    response = api(base + "/prompt", {"prompt": graph})
    prompt_id = response.get("prompt_id")
    if not isinstance(prompt_id, str) or not re.fullmatch(r"[a-fA-F0-9-]+", prompt_id):
        message = "Submission not acknowledged; inspect ComfyUI queue, do not resubmit."
        raise RuntimeError(message)
    update(path, identifier, {"state": "queued", "prompt_id": prompt_id})
    return public(read(path, identifier))


def public(row: dict[str, str]) -> dict[str, object]:
    """Project storage into a credential-free tool result.

    Args:
        row (dict[str, str]): Internal database row.

    Returns:
        dict[str, object]: Request id, state and saved result metadata.

    Raises:
        KeyError: Storage row lacks required columns.
        ValueError: Saved result is malformed JSON.
    """  # noqa: DOC502 - Parser and mapping failures propagate.
    return {
        **object_map(json.loads(row["result"])),
        "request_id": row["id"],
        "state": row["state"],
    }


def poll(path: Location, identifier: str) -> dict[str, object]:
    """Fetch completed output and the exact depth map from the saved endpoint.

    Args:
        path (Location): Database and persistent cache anchor.
        identifier (str): Previously submitted single-frame id.

    Returns:
        dict[str, object]: State, metadata and available output/preview MEDIA paths.

    Raises:
        KeyError: Task or expected history/output data is missing.
        ValueError: History, output image or saved request is malformed.
        RuntimeError: API or PostgreSQL fails.
        OSError: Downloaded output cannot be saved.
        TypeError: Result cannot be serialized.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC502 - Delegated transport and database failures propagate.
    row = read(path, identifier)
    result = public(row)
    if row["state"] in {"completed", "failed", "submitting"}:
        return result
    history = api(row["base"] + "/history/" + row["prompt_id"])
    if row["prompt_id"] not in history:
        return result
    record = object_map(history[row["prompt_id"]])
    state = object_map(record["status"])
    if state.get("status_str") == "error":
        update(path, identifier, {"state": "failed"})
        return {
            **result,
            "state": "failed",
            "hint": "ComfyUI execution failed; inspect logs. No retry submitted.",
        }
    if state.get("completed") is not True:
        return result
    directory = path.parent / "generation-results"
    file = download(row["base"], record, "9", directory / (identifier + ".png"))
    result.update(
        file=file,
        media="MEDIA:" + file,
        generation_seconds=execution_seconds(state),
        state="completed",
    )
    if result["mode"] == "depth":
        preview = download(
            row["base"], record, "19", directory / (identifier + "-depth.png")
        )
        options = object_map(result["options"])
        result.update(
            depth_preview=preview,
            depth_media="MEDIA:" + preview,
            depth_window={
                "start_percent": options["start_percent"],
                "end_percent": options["end_percent"],
                "meaning": (
                    "Denoising interval where the SAME saved depth map "
                    "is applied; no pixel filtering."
                ),
            },
        )
    update(path, identifier, {"state": "completed", "result": canonical(result)})
    return result
