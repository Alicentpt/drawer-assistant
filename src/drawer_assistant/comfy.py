# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Submit fixed GPU-only workflows and retrieve persistent generation jobs."""

import json
import re
import secrets
from contextlib import closing
from pathlib import Path
from typing import cast
from urllib.parse import urlencode

from .network import api, object_map, request
from .store import MAX_FIELD, connect, row_values, transaction

WORKFLOWS = (
    "zimage-w4a8-abliterated",
    "flux2-klein-abliterated",
    "nova-anime-am-v20",
)
EVENT_FIELDS = 2


def execution_seconds(server_status: dict[str, object]) -> str:
    """Measure server execution without queueing, polling or image transfer time.

    Args:
        server_status (dict[str, object]): ComfyUI history status with event messages.

    Returns:
        str: Seconds to two decimals, or empty when timestamps are missing/invalid.
    """
    messages = server_status.get("messages")
    if not isinstance(messages, list):
        return ""
    stamps: dict[str, int] = {}
    # JSON containers are untyped; validate event shape and integer milliseconds.
    for event in cast("list[object]", messages):
        if not isinstance(event, list):
            continue
        if len(cast("list[object]", event)) != EVENT_FIELDS:
            continue
        name, data = cast("list[object]", event)
        if not isinstance(name, str) or not isinstance(data, dict):
            continue
        timestamp = cast("dict[object, object]", data).get("timestamp")
        if (
            isinstance(timestamp, int)
            and not isinstance(timestamp, bool)
            and 0 <= timestamp < 2**63
        ):
            stamps[name] = timestamp
    start_time = stamps.get("execution_start", -1)
    end_time = stamps.get("execution_success", -1)
    return (
        f"{(end_time - start_time) / 1000:.2f}" if 0 <= start_time <= end_time else ""
    )


def workflow(name: str, prompt: str) -> dict[str, object]:
    """Load a fixed GPU preset and inject the unchanged prompt and a random seed.

    Args:
        name (str): Allowlisted workflow; Nova uses 512 square, others 1024 square.
        prompt (str): Nonempty image description, up to 4000 characters.

    Returns:
        dict[str, object]: ComfyUI API graph with batch size one.

    Raises:
        ValueError: Workflow, prompt or packaged JSON is invalid.
        KeyError: Packaged workflow lacks its documented input nodes.
        OSError: Workflow file cannot be read or randomness is unavailable.
        UnicodeError: Workflow file is not UTF-8.
    """  # noqa: DOC503 - Packaged JSON and filesystem errors propagate.
    if name not in WORKFLOWS or not prompt.strip() or len(prompt) > MAX_FIELD:
        message = "Select a supported workflow and provide 1-4000 prompt characters."
        raise ValueError(message)
    graph = object_map(
        json.loads(
            (Path(__file__).parent / "workflows" / f"{name}.api.json").read_text(
                "utf-8"
            )
        )
    )
    object_map(object_map(graph["4"])["inputs"])["text"] = prompt
    seed_node, seed_field = (
        ("10", "noise_seed") if name.startswith("flux2") else ("7", "seed")
    )
    object_map(object_map(graph[seed_node])["inputs"])[seed_field] = secrets.randbits(
        32
    )
    return graph


def start(path: Path, base: str, params: dict[str, str]) -> dict[str, str]:
    """Persist a request before submission so uncertain POSTs are never retried.

    Args:
        path (Path): SQLite database filename.
        base (str): Operator-configured ComfyUI HTTP(S) base URL.
        params (dict[str, str]): Stable id, prompt and optional workflow name.

    Returns:
        dict[str, str]: Job fields; submitting means acceptance is not yet known.

    Raises:
        ValueError: Arguments, API payload, JSON or database schema are invalid.
        KeyError: Required parameters or packaged graph nodes are absent.
        OSError: Database, workflow or random source cannot be accessed.
        UnicodeError: Workflow or server response text is invalid.
        sqlite3.Error: A database operation fails.
        RuntimeError: ComfyUI fails or was started without GPU-only mode.
        TypeError: A packaged API graph cannot be serialized.
    """  # noqa: DOC503 - Includes delegated HTTP/storage errors.
    identifier = params["id"]
    name = params.get("workflow", WORKFLOWS[0])
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier):
        message = "Use a stable generation id of 1-64 letters, digits, _ or -."
        raise ValueError(message)
    graph = workflow(name, params["prompt"])
    with closing(connect(path)) as database:
        with database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT * FROM jobs WHERE id=?", (identifier,)
            ).fetchone()
            if row:
                if row["prompt"] != params["prompt"] or row["workflow"] != name:
                    message = "Generation id already exists with different parameters."
                    raise ValueError(message)
                return row_values(row)
            system = object_map(api(f"{base}/system_stats")["system"])
            argv = system.get("argv", [])
            if not isinstance(argv, list) or "--gpu-only" not in argv:
                message = (
                    "Start ComfyUI with --gpu-only; CPU model offload is forbidden."
                )
                raise RuntimeError(message)
            database.execute(
                "INSERT INTO jobs(id,prompt,workflow) VALUES(?,?,?)",
                (identifier, params["prompt"], name),
            )
        reply = api(f"{base}/prompt", {"prompt": graph})
        prompt_id = reply.get("prompt_id")
        if not isinstance(prompt_id, str) or not re.fullmatch(
            r"[a-fA-F0-9-]+", prompt_id
        ):
            message = "ComfyUI did not acknowledge a prompt id; inspect its queue."
            raise RuntimeError(message)
        with database:
            database.execute(
                "UPDATE jobs SET prompt_id=?,state='queued' WHERE id=?",
                (prompt_id, identifier),
            )
    return {
        "id": identifier,
        "prompt_id": prompt_id,
        "state": "queued",
        "workflow": name,
    }


def save_image(path: Path, base: str, record: dict[str, object], prompt_id: str) -> str:
    """Download a completed workflow's PNG into the local image directory.

    Args:
        path (Path): Database path used to locate its sibling images directory.
        base (str): Configured ComfyUI base URL.
        record (dict[str, object]): Completed history record from ComfyUI.
        prompt_id (str): Validated ComfyUI UUID used as a local filename.

    Returns:
        str: Absolute local filename containing the downloaded PNG.

    Raises:
        ValueError: Response structure or PNG header is invalid.
        KeyError: The server record lacks required image metadata.
        RuntimeError: The image request fails.
        OSError: The image directory or file cannot be written.
    """  # noqa: DOC503 - HTTP and filesystem failures propagate.
    images = object_map(object_map(record["outputs"])["9"])["images"]
    if not isinstance(images, list) or not images:
        message = "ComfyUI completed without an image."
        raise ValueError(message)
    # JSON list elements are untyped; object_map validates the selected element.
    image = object_map(cast("list[object]", images)[0])
    query = urlencode(
        {key: str(image[key]) for key in ("filename", "subfolder", "type")}
    )
    content = request(f"{base}/view?{query}")
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        message = "Expected a PNG image from the fixed SaveImage workflow."
        raise ValueError(message)
    destination = path.parent / "images" / f"{prompt_id}.png"
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.replace(destination)
    return str(destination.resolve())


def status(
    path: Path, base: str, identifier: str, *, workflow_name: str = ""
) -> dict[str, str]:
    """Poll a known generation and save its image locally for Hermes delivery.

    Args:
        path (Path): SQLite database filename; images are stored beside it.
        base (str): Operator-configured ComfyUI HTTP(S) base URL.
        identifier (str): Previously submitted stable generation id.
        workflow_name (str, default=""): Required workflow; empty allows legacy jobs.

    Returns:
        dict[str, str]: State, file, MEDIA marker and server generation_seconds;
            empty timing means the server supplied no valid timestamps.

    Raises:
        KeyError: Job or required server response field is absent.
        ValueError: Response, image, schema or selected model does not match the job.
        OSError: Database directory or generated image cannot be written.
        UnicodeError: A server response cannot be decoded.
        sqlite3.Error: A database operation fails.
        RuntimeError: The HTTP request fails.
        TypeError: An API request cannot be serialized.
    """  # noqa: DOC503 - Includes delegated HTTP/storage errors.
    with transaction(path) as database:
        row = database.execute(
            "SELECT * FROM jobs WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        result = row_values(row)
        if workflow_name and result["workflow"] != workflow_name:
            message = "Use the model tool that originally created this job."
            raise ValueError(message)
        if result["state"] in {"submitting", "failed"}:
            return result
        if not result["file"]:
            history = api(f"{base}/history/{result['prompt_id']}")
            if result["prompt_id"] not in history:
                return result
            record = object_map(history[result["prompt_id"]])
            server_status = object_map(record.get("status"))
            if server_status.get("status_str") == "error":
                database.execute(
                    "UPDATE jobs SET state='failed' WHERE id=?", (identifier,)
                )
                result["state"] = "failed"
                return result
            if server_status.get("completed") is not True:
                return result
            result.update(
                file=save_image(path, base, record, result["prompt_id"]),
                state="completed",
                generation_seconds=execution_seconds(server_status),
            )
            database.execute(
                "UPDATE jobs SET state='completed',file=?,generation_seconds=? "
                "WHERE id=?",
                (result["file"], result["generation_seconds"], identifier),
            )
        result["media"] = f"MEDIA:{result['file']}"
        return result
