# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Verify independent optional adapters and preserve the generation presets."""

from pathlib import Path
from typing import cast

import pytest

from drawer_assistant.anima_graph import build
from drawer_assistant.network import object_map

FOLDER = Path(__file__).resolve().parents[1] / "experiments" / "anima-controls"
DEFAULT_SIZE = 512


def dependencies(graph: dict[str, object], identifier: str) -> set[str]:
    """Follow execution dependencies using ComfyUI's lazy switch contract.

    Args:
        graph (dict[str, object]): Repository-owned API graph under test.
        identifier (str): Output node from which execution starts.

    Returns:
        set[str]: Node ids required by the selected switch configuration.

    Raises:
        KeyError: A graph link or required switch input is missing.
        ValueError: A node or its inputs is not an object.
        RecursionError: The generated dependency graph contains a cycle.
    """  # noqa: DOC502 - Validation and recursive traversal errors propagate.
    node = object_map(graph[identifier])
    inputs = object_map(node["inputs"])
    if node["class_type"] == "LazySwitchKJ":
        enabled = inputs["switch"]
        if isinstance(enabled, list):
            # Depth's positive and negative switches share a Boolean input node.
            identifier = str(cast("list[object]", enabled)[0])
            enabled = object_map(object_map(graph[identifier])["inputs"])["value"]
        inputs = {"selected": inputs["on_true" if enabled else "on_false"]}
    result = {identifier}
    for value in inputs.values():
        if isinstance(value, list):
            # Graph inputs contain only scalar widgets or two-field node links.
            link = cast("list[object]", value)
            result |= dependencies(graph, str(link[0]))
    return result


@pytest.mark.parametrize("flags", range(16))
def test_optional_adapters(flags: int) -> None:
    """Exclude an entire disabled branch, including loaders, LoRA and detector.

    Args:
        flags (int): Bit mask covering all sixteen combinations of four controls.

    Returns:
        None: Only enabled branches are reachable from the final image output.

    Raises:
        AssertionError: Disabled adapter work remains reachable or a preset changes.
        OSError: A source workflow cannot be read.
        UnicodeError: A source workflow is not UTF-8.
        ValueError: Workflow JSON or object validation fails.
        KeyError: A required graph node or link is missing.
        TypeError: A source graph has an incompatible JSON structure.
        RecursionError: A generated graph contains a cycle.
    """  # noqa: DOC502 - Builder, traversal and assertion failures propagate.
    graph = build(FOLDER)
    for index, key in enumerate(("150", "250", "350")):
        object_map(object_map(graph[key])["inputs"])["switch"] = bool(
            flags & (1 << index)
        )
    object_map(object_map(graph["440"])["inputs"])["value"] = bool(flags & 8)
    active = dependencies(graph, "9")
    for index, loader in enumerate(("111", "211", "314", "412")):
        assert (loader in active) == bool(flags & (1 << index))
    assert ("411" in active) == bool(flags & 8)
    sampler = object_map(object_map(graph["7"])["inputs"])
    assert sampler["positive"] == ["450", 0]
    assert sampler["negative"] == ["451", 0]
    for key in ("450", "451"):
        assert object_map(object_map(graph[key])["inputs"])["switch"] == ["440", 0]
    assert object_map(object_map(graph["6"])["inputs"])["batch_size"] == 1
    assert object_map(object_map(graph["99"])["inputs"])["model"] == ["350", 0]
    assert object_map(object_map(graph["1"])["inputs"])["weight_dtype"] == "fp8_e4m3fn"
    assert object_map(object_map(graph["6"])["inputs"])["height"] == DEFAULT_SIZE
    assert (
        object_map(object_map(graph["212"])["inputs"])["target_height"] == DEFAULT_SIZE
    )
    assert object_map(object_map(graph["312"])["inputs"])["height"] == DEFAULT_SIZE
