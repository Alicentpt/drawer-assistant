# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Check live ComfyUI capabilities without submitting GPU jobs."""

import json
from pathlib import Path
from typing import cast

from drawer_assistant.network import api, object_map

from .presets import array


def inspect_server(base: str) -> dict[str, object]:
    """Report required node/model availability separately for each mode.

    Args:
        base (str): Operator-configured ComfyUI HTTP(S) endpoint.

    Returns:
        dict[str, object]: GPU-only flag and per-mode missing nodes/models/settings.

    Raises:
        RuntimeError: Server cannot be reached.
        ValueError: Server or packaged JSON is invalid.
        OSError: Packaged graphs cannot be read.
        KeyError: Required graph or server fields are absent.
        TypeError: Transport cannot serialize its request.
    """  # noqa: DOC502 - Transport, graph and mapping errors propagate.
    stats, available = api(base + "/system_stats"), api(base + "/object_info")
    gpu_only = "--gpu-only" in array(object_map(stats["system"]).get("argv", []))
    modes: dict[str, object] = {}
    for file in sorted((Path(__file__).parent / "workflows").glob("*.api.json")):
        graph = object_map(json.loads(file.read_text("utf-8")))
        missing: list[str] = []
        for value in graph.values():
            entry = object_map(value)
            kind = str(entry["class_type"])
            if kind not in available:
                missing.append(kind)
                continue
            required = object_map(
                object_map(object_map(available[kind])["input"]).get("required", {})
            )
            if kind == "LoadImage":
                continue
            for field, content in object_map(entry["inputs"]).items():
                declaration = required.get(field)
                if (
                    isinstance(declaration, list)
                    and cast("list[object]", declaration)
                    and isinstance(cast("list[object]", declaration)[0], list)
                    and not isinstance(content, list)
                    and content not in array(cast("list[object]", declaration)[0])
                ):
                    missing.append(f"{kind}.{field}={content}")
        if "ImageScale" not in available:
            missing.append("ImageScale")
        modes[file.name.split(".")[0]] = {
            "ready": gpu_only and not missing,
            "missing": sorted(set(missing)),
        }
    return {
        "gpu_only": gpu_only,
        "modes": modes,
        "model": "novaAnimeAM_v20.safetensors",
    }
