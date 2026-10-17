# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Advance persistent reference-aware batches one GPU frame at a time."""

import json
from typing import TYPE_CHECKING

from drawer_assistant.image_sheet import sheet
from drawer_assistant.network import object_map

from . import jobs
from .presets import array, canonical, normalize

if TYPE_CHECKING:
    from drawer_assistant.database import Location


def prepare(path: Location, params: dict[str, object]) -> dict[str, object]:
    """Merge batch defaults with every frame's own hyperparameters and references.

    Args:
        path (Location): Persistent input cache anchor.
        params (dict[str, object]): Schema-validated batch request.

    Returns:
        dict[str, object]: Immutable normalized frames and assembly options.

    Raises:
        ValueError: Frame, options, image or reference count is invalid.
        KeyError: Required batch/frame fields are missing.
        OSError: References cannot be cached.
        TypeError: Request cannot be serialized.
    """  # noqa: DOC503 - Delegated validation and cache failures propagate.
    raw = array(params["frames"])
    frames: list[dict[str, object]] = []
    for item in raw:
        frame = object_map(item)
        frames.append(
            normalize(
                str(params["mode"]),
                {
                    "prompt": frame["prompt"],
                    "references": frame.get("references", params.get("references", [])),
                    "options": {
                        **object_map(params.get("defaults", {})),
                        **object_map(frame.get("options", {})),
                    },
                },
                path.parent / "generation-inputs",
            )
        )
    labels = array(params.get("labels", []))
    if labels and len(labels) != len(frames):
        message = "labels must match the number of frames."
        raise ValueError(message)
    return {
        "mode": "batch",
        "frames": frames,
        "assemble": params.get("assemble", True),
        "layout": {
            key: params[key]
            for key in ("columns", "labels", "cell_size", "gap")
            if key in params
        },
    }


def advance(path: Location, identifier: str, executable: str) -> dict[str, object]:
    """Poll children, submit only the next frame and assemble the complete batch.

    Args:
        path (Location): Shared task database and output cache anchor.
        identifier (str): Existing batch id.
        executable (str): Configured ImageMagick executable or empty for PATH.

    Returns:
        dict[str, object]: Ordered progress with per-frame settings, depth previews
            and final collage only when every frame succeeds.

    Raises:
        ValueError: Saved spec, image, graph or layout is invalid.
        KeyError: Batch, child or required server data is missing.
        OSError: Reference/output files or ImageMagick cannot be accessed.
        RuntimeError: ComfyUI, ImageMagick or PostgreSQL fails.
        TypeError: Saved spec/result cannot be serialized.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC502 - Delegated generation, assembly and storage failures propagate.
    row = jobs.read(path, identifier)
    if row["state"] == "completed":
        return jobs.public(row)
    spec = object_map(json.loads(row["spec"]))
    raw = array(spec["frames"])
    frames: list[dict[str, object]] = []
    for index, item in enumerate(raw):
        child = f"{identifier}-p{index + 1:02}"
        try:
            jobs.read(path, child)
        except KeyError:
            frame = jobs.submit(path, row["base"], child, object_map(item))
        else:
            frame = jobs.poll(path, child)
        frames.append(frame)
        if frame["state"] != "completed":
            break
    completed = sum(frame["state"] == "completed" for frame in frames)
    result: dict[str, object] = {
        "request_id": identifier,
        "mode": "batch",
        "expected_images": len(raw),
        "completed_images": completed,
        "frames": frames,
        "state": "queued",
    }
    if any(frame["state"] in {"failed", "submitting"} for frame in frames):
        result["state"] = "needs_attention"
    if completed == len(raw):
        result["state"] = "completed"
        if spec["assemble"]:
            result.update(
                sheet(
                    path.parent / "image-sheets",
                    {
                        **object_map(spec["layout"]),
                        "files": [str(frame["file"]) for frame in frames],
                    },
                    executable,
                )
            )
    jobs.update(
        path, identifier, {"state": str(result["state"]), "result": canonical(result)}
    )
    return result
