# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Persist asynchronous Qwen jobs across agent calls without duplicate submissions."""

import hashlib
import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit
from uuid import uuid4

from dotenv import dotenv_values

from .alibaba_inputs import PNG, InputError, endpoint, payload
from .network import api, object_map, request
from .store import row_values, transaction

if TYPE_CHECKING:
    from pathlib import Path

TOOLS = {
    "drawer_qwen_image_3": "qwen-image-3.0",
    "drawer_qwen_image_3_pro": "qwen-image-3.0-pro",
    "drawer_qwen_image_2_1_pro": "qwen-image-2.1-pro",
}


def read_job(path: Path, identifier: str, model: str) -> dict[str, str]:
    """Read a durable job and enforce its association with the selected model tool.

    Args:
        path (Path): Artist SQLite database.
        identifier (str): Previously chosen stable request identifier.
        model (str): Fixed model of the calling tool.

    Returns:
        dict[str, str]: Complete internal database row.

    Raises:
        InputError: Job is missing or belongs to another model.
        OSError: Database directory cannot be accessed.
        ValueError: Database schema is unsupported.
        sqlite3.Error: Reading the job or migrating storage fails.
    """  # noqa: DOC503 - Storage errors propagate.
    with transaction(path) as database:
        row = database.execute(
            "SELECT * FROM alibaba_jobs WHERE id=?", (identifier,)
        ).fetchone()
    if row is None or row["model"] != model:
        message = "No job with this id for the selected model tool."
        raise InputError(message)
    return row_values(row)


def start(path: Path, config: dict[str, str], params: dict[str, str]) -> dict[str, str]:
    """Commit an intent before its paid POST; never resubmit a repeated identifier.

    Args:
        path (Path): Artist database filename.
        config (dict[str, str]): Validated base, secret key and fixed model.
        params (dict[str, str]): Stable id and image generation arguments.

    Returns:
        dict[str, str]: Durable job; submitting means acceptance is unknown.

    Raises:
        InputError: Identifier, arguments or reuse conflicts with a previous request.
        ValueError: Inputs, response JSON or database schema are malformed.
        KeyError: Required config, arguments or response fields are absent.
        OSError: A reference or database cannot be accessed.
        UnicodeError: API response encoding is invalid.
        TypeError: Request cannot be serialized.
        RuntimeError: HTTP fails, times out or returns no valid task identifier.
        sqlite3.Error: Storing or retrieving the job fails.
    """  # noqa: DOC503 - API, validation and storage failures propagate.
    identifier = params.get("id", "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier):
        message = "Use a stable id of 1-64 letters, digits, _ or -."
        raise InputError(message)
    body = payload(config["model"], params)
    fingerprint = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    with transaction(path) as database:
        database.execute("BEGIN IMMEDIATE")
        row = database.execute(
            "SELECT * FROM alibaba_jobs WHERE id=?", (identifier,)
        ).fetchone()
        if row:
            if row["fingerprint"] != fingerprint or row["base"] != config["base"]:
                message = "Job id already exists with different parameters or endpoint."
                raise InputError(message)
            return row_values(row)
        database.execute(
            "INSERT INTO alibaba_jobs(id,model,base,fingerprint,request) "
            "VALUES(?,?,?,?,?)",
            (
                identifier,
                config["model"],
                config["base"],
                fingerprint,
                json.dumps(params, ensure_ascii=False),
            ),
        )
    reply = api(
        f"{config['base']}/api/v1/services/aigc/image-generation/generation",
        body,
        headers={
            "Authorization": f"Bearer {config['key']}",
            "X-DashScope-Async": "enable",
        },
    )
    task_id = object_map(reply.get("output", {})).get("task_id")
    if not isinstance(task_id, str) or not re.fullmatch(
        r"[a-zA-Z0-9_-]{1,128}", task_id
    ):
        message = "Alibaba did not acknowledge a task id; inspect the console."
        raise RuntimeError(message)
    with transaction(path) as database:
        database.execute(
            "UPDATE alibaba_jobs SET task_id=?,state='PENDING' WHERE id=?",
            (task_id, identifier),
        )
    return read_job(path, identifier, config["model"])


def first_object(value: object) -> dict[str, object]:
    """Read the first object of a nonempty API array.

    Args:
        value (object): Untrusted decoded response field.

    Returns:
        dict[str, object]: Validated first element.

    Raises:
        ValueError: Field is not a nonempty list of objects.
    """  # noqa: DOC503 - Mapping validation also raises ValueError.
    if not isinstance(value, list) or not value:
        message = "Expected a nonempty response array."
        raise ValueError(message)
    # JSON arrays are untyped; object_map validates the first element.
    return object_map(cast("list[object]", value)[0])


def save_result(path: Path, output: dict[str, object], identifier: str) -> str:
    """Save a completed PNG locally before the provider's 24-hour URL expires.

    Args:
        path (Path): Database whose sibling images directory stores results.
        output (dict[str, object]): Successful DashScope task output.
        identifier (str): Validated locally generated job id.

    Returns:
        str: Absolute PNG filename for Hermes delivery.

    Raises:
        ValueError: Output shape, HTTPS image origin or PNG signature is invalid.
        KeyError: The response lacks image metadata.
        RuntimeError: Download fails or exceeds the transport size limit.
        OSError: The output directory or atomic file replacement fails.
    """  # noqa: DOC503 - API parsing, downloading and filesystem errors propagate.
    message = object_map(first_object(output["choices"])["message"])
    url = str(first_object(message["content"])["image"])
    address = urlsplit(url)
    if address.scheme != "https" or not (address.hostname or "").endswith(
        ".aliyuncs.com"
    ):
        error = "Expected an Alibaba HTTPS image URL."
        raise ValueError(error)
    data = request(url)  # Never send the API key to image download hosts.
    if not data.startswith(PNG):
        error = "Expected a generated PNG image."
        raise ValueError(error)
    destination = path.parent / "images" / f"alibaba-{identifier}.png"
    destination.parent.mkdir(exist_ok=True)
    temporary = destination.with_suffix(f".{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return str(destination.resolve())


def execution_seconds(output: dict[str, object]) -> str:
    """Calculate execution seconds from two server timestamps in the same UTC+8 zone.

    Args:
        output (dict[str, object]): Task output with scheduled_time and end_time.

    Returns:
        str: Duration to two decimals, or empty for absent, invalid or reversed times.
    """
    try:
        started = datetime.fromisoformat(str(output.get("scheduled_time", "")))
        finished = datetime.fromisoformat(str(output.get("end_time", "")))
        seconds = (finished - started).total_seconds()
    except ValueError, TypeError, OverflowError:
        return ""
    return f"{seconds:.2f}" if seconds >= 0 else ""


def status(path: Path, config: dict[str, str], identifier: str) -> dict[str, str]:
    """Poll a persisted task once, preserving completed results across restarts.

    Args:
        path (Path): Artist SQLite database.
        config (dict[str, str]): Validated endpoint, secret key and fixed model.
        identifier (str): Existing local request id.

    Returns:
        dict[str, str]: Job record including state, output path and server duration.

    Raises:
        InputError: Job is absent, owned by another model, or endpoint has changed.
        ValueError: Schema, task state, response shape or output PNG is invalid.
        KeyError: Config or API response fields are missing.
        UnicodeError: API JSON cannot be decoded.
        RuntimeError: Polling or downloading fails.
        OSError: Local result or database cannot be accessed.
        sqlite3.Error: Reading or updating the job fails.
    """  # noqa: DOC503 - Network and storage failures propagate.
    job = read_job(path, identifier, config["model"])
    if job["state"] not in {"PENDING", "RUNNING"}:
        return job
    if job["base"] != config["base"]:
        message = "Restore this job's original DASHSCOPE_BASE_URL before polling."
        raise InputError(message)
    output = object_map(
        api(
            f"{config['base']}/api/v1/tasks/{job['task_id']}",
            headers={"Authorization": f"Bearer {config['key']}"},
        )["output"]
    )
    state = str(output["task_status"])
    if state not in {
        "PENDING",
        "RUNNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELED",
        "UNKNOWN",
    }:
        message = "Unexpected Alibaba task state."
        raise ValueError(message)
    filename = save_result(path, output, identifier) if state == "SUCCEEDED" else ""
    code = str(output.get("code", ""))
    code = code if re.fullmatch(r"[a-zA-Z0-9_.-]{0,128}", code) else "ProviderError"
    with transaction(path) as database:
        database.execute(
            "UPDATE alibaba_jobs SET state=?,file=?,generation_seconds=?,error_code=? "
            "WHERE id=?",
            (state, filename, execution_seconds(output), code, identifier),
        )
    return read_job(path, identifier, config["model"])


def run(
    path: Path, profile: Path, model: str, params: dict[str, str]
) -> dict[str, str]:
    """Dispatch a model-specific tool using only its own profile's credentials.

    Args:
        path (Path): Artist database filename.
        profile (Path): Installed Hermes profile, never inferred from ambient env.
        model (str): Fixed registered model name.
        params (dict[str, str]): start/status action and its arguments.

    Returns:
        dict[str, str]: Public metadata and MEDIA marker, without keys or references.

    Raises:
        InputError: Key, endpoint, action or image arguments need correction.
        ValueError: Config, API JSON, payload or database schema is invalid.
        KeyError: A required tool argument or response field is absent.
        OSError: Profile, input image, result or database cannot be accessed.
        UnicodeError: Local text or API response cannot be decoded.
        TypeError: A request cannot be serialized.
        RuntimeError: API submission, polling or downloading fails.
        sqlite3.Error: Database operation fails.
    """  # noqa: DOC503 - Delegated validation, network and storage errors propagate.
    settings = dotenv_values(profile / ".env", interpolate=False, encoding="utf-8-sig")
    key = (settings.get("DASHSCOPE_API_KEY") or "").strip()
    if not key or "\n" in key or "\r" in key:
        message = "Fill DASHSCOPE_API_KEY in project .env and run uv run drawer-setup."
        raise InputError(message)
    config = {
        "key": key,
        "model": model,
        "base": endpoint(
            settings.get("DASHSCOPE_BASE_URL") or "https://dashscope-intl.aliyuncs.com"
        ),
    }
    if params.get("action") == "start":
        job = start(path, config, params)
    elif params.get("action") == "status":
        job = status(path, config, params["id"])
    else:
        message = "Use start or status."
        raise InputError(message)
    public = {
        name: job[name]
        for name in (
            "id",
            "model",
            "task_id",
            "state",
            "file",
            "generation_seconds",
            "error_code",
        )
    }
    if job["file"]:
        public["media"] = f"MEDIA:{job['file']}"
    return public
