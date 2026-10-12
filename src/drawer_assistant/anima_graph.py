# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Build an experimental Anima graph with three independent lazy switches."""

import json
from pathlib import Path
from typing import cast

LINK_FIELDS = 2
FIRST_BRANCH_NODE = 10


def rewire(value: object, ids: dict[str, str], model: list[str | int]) -> object:
    """Reconnect a copied adapter branch to its own ids and preceding model.

    Args:
        value (object): JSON value from a validated experimental graph.
        ids (dict[str, str]): Branch-local node id replacements.
        model (list[str | int]): Upstream model link used instead of the base loader.

    Returns:
        object: Deep copy with updated links and unchanged scalar parameters.
    """
    if value == ["1", 0]:
        return model.copy()
    if isinstance(value, list):
        # JSON containers are untyped; only node-link shapes receive id replacement.
        items = cast("list[object]", value)
        if len(items) == LINK_FIELDS and isinstance(items[0], str) and items[0] in ids:
            return [ids[items[0]], items[1]]
        return [rewire(item, ids, model) for item in items]
    if isinstance(value, dict):
        # Input files are our fixed string-keyed JSON templates.
        return {
            key: rewire(item, ids, model)
            for key, item in cast("dict[str, object]", value).items()
        }
    return value


def build(folder: Path) -> dict[str, object]:
    """Combine style, character and pose without loading disabled branches.

    Args:
        folder (Path): Directory containing the three pinned standalone API graphs.

    Returns:
        dict[str, object]: Graph with Style/Character/Pose booleans on lazy switches
            150/250/350. False bypasses the entire adapter, including its LoRA.

    Raises:
        OSError: A source workflow cannot be read.
        UnicodeError: A workflow is not UTF-8.
        ValueError: A source JSON document is invalid.
        KeyError: A fixed graph node or model link is missing.
        TypeError: A source graph has an incompatible JSON structure.
    """  # noqa: DOC502 - Fixed graph file and JSON decoding errors propagate.
    # Templates are repository-owned; structural validation also runs on ComfyUI.
    graph = cast(
        "dict[str, object]",
        json.loads((folder / "character.api.json").read_text("utf-8")),
    )
    graph = {key: value for key, value in graph.items() if int(key) < FIRST_BRANCH_NODE}
    model: list[str | int] = ["1", 0]
    for index, mode in enumerate(("style", "character", "pose"), 1):
        source = cast(
            "dict[str, dict[str, object]]",
            json.loads((folder / f"{mode}.api.json").read_text("utf-8")),
        )
        branch = {
            key: value
            for key, value in source.items()
            if int(key) >= FIRST_BRANCH_NODE
            and value["class_type"] != "ModelSamplingAuraFlow"
        }
        ids = {key: str(index * 100 + int(key)) for key in branch}
        graph.update(
            {ids[key]: rewire(value, ids, model) for key, value in branch.items()}
        )
        branch_output = {"style": "12", "character": "13", "pose": "15"}[mode]
        switch = str(index * 100 + 50)
        graph[switch] = {
            "class_type": "LazySwitchKJ",
            "_meta": {"title": f"Enable {mode.upper()} (false = completely off)"},
            "inputs": {
                "switch": True,
                "on_false": model,
                "on_true": [ids[branch_output], 0],
            },
        }
        model = [switch, 0]
    graph["99"] = {
        "class_type": "ModelSamplingAuraFlow",
        "inputs": {"model": model, "shift": 3.0},
    }
    sampler = cast("dict[str, object]", graph["7"])
    cast("dict[str, object]", sampler["inputs"])["model"] = ["99", 0]
    output = cast("dict[str, object]", graph["9"])
    cast("dict[str, object]", output["inputs"])["filename_prefix"] = (
        "Jessica/All_In_One"
    )
    return graph


if __name__ == "__main__":
    directory = Path(__file__).resolve().parents[2] / "experiments" / "anima-controls"
    (directory / "all-in-one.api.json").write_text(
        json.dumps(build(directory), ensure_ascii=False, indent=2) + "\n", "utf-8"
    )
