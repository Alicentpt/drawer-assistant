# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Persist multi-image Anima requests and assemble only fully completed batches."""

import json
import re
from time import monotonic, sleep
from typing import TYPE_CHECKING

from . import comfy
from .image_sheet import MAX_LABEL, integer, magick_path, sheet, strings
from .network import object_map
from .store import MAX_FIELD, transaction

if TYPE_CHECKING:
    from .database import Location

MIN_FRAMES = 2


def multipanel(prompt: str) -> bool:
    """Recognize explicit panel-count instructions accidentally sent to one image.

    Args:
        prompt (str): Scene text supplied to the single-image generator.

    Returns:
        bool: Whether a numbered panel list or numeric grid layout is requested.
            Ordinary background grids and unnumbered comic-style art remain allowed.
    """
    return bool(
        re.search(
            r"\b(?:[2-9]|1[0-6]|two|three|four|five|six|seven|eight|nine|ten|"
            r"eleven|twelve|thirteen|fourteen|fifteen|sixteen)\s+panels?\b|"
            r"\b\d{1,2}\s*[x\u00d7*]\s*\d{1,2}\s+(?:grid|panels?|contact sheet)\b|"
            r"\bpanel\s*[2-9]\s*:",
            prompt,
            re.IGNORECASE,
        )
    )


def specification(params: dict[str, object]) -> dict[str, object]:
    """Validate an immutable multi-image request before submitting any GPU work.

    Args:
        params (dict[str, object]): Prompts, labels and sheet layout supplied on start.

    Returns:
        dict[str, object]: Normalized two-to-sixteen prompts and bounded layout.

    Raises:
        ValueError: Prompts, labels, panel counts or layout bounds are invalid.
    """  # noqa: DOC503 - Shared argument validation errors propagate.
    prompts = strings(params, "prompts")
    labels = strings(params, "labels")
    if len(prompts) < MIN_FRAMES or any(
        not prompt.strip() or len(prompt) > MAX_FIELD or multipanel(prompt)
        for prompt in prompts
    ):
        message = "Provide 2..16 single-frame prompts, up to 4000 characters each."
        raise ValueError(message)
    if (labels and len(labels) != len(prompts)) or any(
        len(label) > MAX_LABEL or any(char in label for char in "%@\r\n")
        for label in labels
    ):
        message = "Provide one short, single-line label per frame, without % or @."
        raise ValueError(message)
    return {
        "prompts": prompts,
        "labels": labels,
        "columns": integer(params, "columns", 3, 1, 8),
        "cell_size": integer(params, "cell_size", 512, 128, 1024),
        "gap": integer(params, "gap", 16, 0, 64),
    }


def start_batch(
    path: Location, base: str, identifier: str, spec: dict[str, object]
) -> None:
    """Persist a batch intent, then submit each independently idempotent scene.

    Args:
        path (Location): Shared artist database filename.
        base (str): Configured Anima ComfyUI endpoint.
        identifier (str): Validated stable batch id, at most 48 characters.
        spec (dict[str, object]): Validated prompts and assembly layout.

    Returns:
        None: All submissions are acknowledged or existing job states are reused.
            An uncertain submitting child stops submission of further scenes.

    Raises:
        ValueError: Existing batch parameters differ, or delegated validation fails.
        KeyError: Required request, workflow or server fields are missing.
        OSError: Database, workflow or random source cannot be accessed.
        UnicodeError: Workflow or server data cannot be decoded.
        RuntimeError: PostgreSQL operations fail, or GPU-only preflight or
            ComfyUI request fails.
        TypeError: Request data cannot be serialized.
        sqlite3.Error: Database operations fail.
    """  # noqa: DOC503 - Generation, serialization and storage errors propagate.
    serialized = json.dumps(spec, sort_keys=True, ensure_ascii=False)
    with transaction(path) as database:
        previous = database.execute(
            "SELECT spec FROM image_batches WHERE id=?", (identifier,)
        ).fetchone()
        if previous and previous["spec"] != serialized:
            message = "Batch id already exists with different parameters."
            raise ValueError(message)
        database.execute(
            "INSERT INTO image_batches(id,spec) VALUES(?,?) ON CONFLICT(id) DO NOTHING",
            (identifier, serialized),
        )
    for index, prompt in enumerate(strings(spec, "prompts"), 1):
        result = comfy.start(
            path,
            base,
            {
                "id": f"{identifier}-p{index:02}",
                "prompt": prompt,
                "workflow": comfy.WORKFLOWS[2],
            },
        )
        if result["state"] == "submitting":
            break


def batch_status(
    path: Location, base: str, identifier: str, executable: str
) -> dict[str, object]:
    """Read child jobs and publish a cached sheet only after every frame succeeds.

    Args:
        path (Location): Shared artist database containing batch intent and child jobs.
        base (str): Configured Anima ComfyUI endpoint.
        identifier (str): Previously persisted stable batch id.
        executable (str): Optional absolute ImageMagick executable path.

    Returns:
        dict[str, object]: Batch state, expected/completed counts and ordered child
            states/timings. Completed batches include a local file, MEDIA and
            assembly_seconds. Failed, uncertain or missing children never produce
            a partial sheet; missing children are marked not_submitted.

    Raises:
        KeyError: Batch, workflow or required server field is missing.
        ValueError: Saved metadata, response, schema, image or layout is invalid.
        OSError: Database, image or ImageMagick files cannot be accessed.
        UnicodeError: A server response cannot be decoded.
        RuntimeError: PostgreSQL operations fail, or a ComfyUI request or
            ImageMagick assembly fails.
        TypeError: An API request cannot be serialized.
        sqlite3.Error: Reading or updating batch/job storage fails.
    """  # noqa: DOC503 - Delegated status, storage and assembly errors propagate.
    with transaction(path) as database:
        row = database.execute(
            "SELECT * FROM image_batches WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        spec = object_map(json.loads(row["spec"]))
        existing = {
            item["id"]
            for item in database.execute(
                "SELECT id FROM jobs WHERE id LIKE ?",
                (identifier + "-p%",),
            )
        }
    frames = [
        comfy.status(path, base, child, workflow_name=comfy.WORKFLOWS[2])
        if child in existing
        else {"id": child, "state": "not_submitted"}
        for child in (
            f"{identifier}-p{index:02}"
            for index in range(1, len(strings(spec, "prompts")) + 1)
        )
    ]
    complete = sum(frame["state"] == "completed" for frame in frames)
    state = "completed" if complete == len(frames) else "queued"
    if any(
        frame["state"] in {"failed", "submitting", "not_submitted"} for frame in frames
    ):
        state = "needs_attention"
    result: dict[str, object] = {
        "id": identifier,
        "state": state,
        "expected_images": len(frames),
        "completed_images": complete,
        "image_count": len(frames),
        "columns": spec["columns"],
        "frames": [
            {
                key: value
                for key, value in frame.items()
                if key
                in {
                    "id",
                    "state",
                    "file",
                    "generation_seconds",
                }
            }
            for frame in frames
        ],
    }
    if state != "completed":
        return result
    if not row["file"]:
        assembled = sheet(
            path.parent / "image-sheets",
            {
                **{key: value for key, value in spec.items() if key != "prompts"},
                "files": [frame["file"] for frame in frames],
            },
            executable,
        )
        with transaction(path) as database:
            database.execute(
                "UPDATE image_batches SET file=?,assembly_seconds=? WHERE id=?",
                (
                    assembled["file"],
                    assembled["assembly_seconds"],
                    identifier,
                ),
            )
        return {**result, **assembled}
    return {
        **result,
        "file": row["file"],
        "media": f"MEDIA:{row['file']}",
        "assembly_seconds": row["assembly_seconds"],
    }


def batch(
    path: Location, base: str, params: dict[str, object], executable: str
) -> dict[str, object]:
    """Start or poll a persistent Anima batch, waiting with bounded internal pauses.

    Args:
        path (Location): Artist database path.
        base (str): Anima ComfyUI HTTP(S) endpoint.
        params (dict[str, object]): action=start/status and stable id (1..48 ASCII
            letters/digits/_/-). Start also requires prompts, accepts labels and
            columns/cell_size/gap. Status needs only action/id.
        executable (str): Optional absolute ImageMagick executable path.

    Returns:
        dict[str, object]: Current batch progress and, on complete success, PNG.
            Start queues individual jobs without waiting. Status checks every five
            seconds for at most 25 seconds plus HTTP time. No partial sheet is sent.

    Raises:
        ValueError: Arguments, metadata, workflow, responses or layout are invalid.
        KeyError: Batch, required job or API field is missing.
        OSError: Local files, system clock or random source cannot be accessed.
        UnicodeError: Workflow or server responses cannot be decoded.
        RuntimeError: PostgreSQL operations fail, or ImageMagick is missing
            or GPU preflight, HTTP or assembly fails.
        TypeError: Request data cannot be serialized.
        sqlite3.Error: Batch or generation storage fails.
    """  # noqa: DOC503 - Delegated generation, storage and assembly errors propagate.
    identifier = params.get("id")
    action = params.get("action")
    if not isinstance(identifier, str) or not re.fullmatch(
        r"[a-zA-Z0-9_-]{1,48}", identifier
    ):
        message = "Use a stable batch id of 1..48 letters, digits, _ or -."
        raise ValueError(message)
    allowed = {"action", "id"} | (
        {"prompts", "labels", "columns", "cell_size", "gap"}
        if action == "start"
        else set[str]()
    )
    if action not in {"start", "status"} or set(params) - allowed:
        message = "Use start with separate prompts or status with the existing id only."
        raise ValueError(message)
    if action == "start":
        spec = specification(params)
        if not magick_path(executable):
            message = "Install ImageMagick before starting a multi-image batch."
            raise RuntimeError(message)
        start_batch(path, base, identifier, spec)
    deadline = monotonic() + (25 if action == "status" else 0)
    while True:
        result = batch_status(path, base, identifier, executable)
        remaining = deadline - monotonic()
        if result["state"] != "queued" or remaining <= 0:
            return result
        sleep(min(5, remaining))
