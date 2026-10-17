# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Build fixed Nova graphs with early stretch resizing and explicit controls."""

import json
from pathlib import Path

from drawer_assistant.network import object_map

from .presets import canonical


def inputs(graph: dict[str, object], identifier: str) -> dict[str, object]:
    """Access validated mutable node inputs.

    Args:
        graph (dict[str, object]): API graph loaded from packaged JSON.
        identifier (str): Existing node identifier.

    Returns:
        dict[str, object]: Mutable input mapping.

    Raises:
        KeyError: Node or inputs field is absent.
        ValueError: Mapping is malformed.
    """  # noqa: DOC502 - Mapping access and validation errors propagate.
    return object_map(object_map(graph[identifier])["inputs"])


def node(kind: str, values: dict[str, object]) -> dict[str, object]:
    """Create an API node without editor metadata.

    Args:
        kind (str): Fixed ComfyUI class name.
        values (dict[str, object]): Validated node parameters.

    Returns:
        dict[str, object]: Serializable API node.
    """
    return {"class_type": kind, "inputs": values}


def build(spec: dict[str, object], names: list[str], seed: int) -> dict[str, object]:
    """Populate the selected workflow with normalized references and settings.

    Args:
        spec (dict[str, object]): Normalized immutable generation specification.
        names (list[str]): Uploaded ComfyUI image filenames in reference order.
        seed (int): Persisted unsigned sampling seed.

    Returns:
        dict[str, object]: API graph with SaveImage 9 and depth preview 19.

    Raises:
        OSError: Packaged graph cannot be read.
        ValueError: Graph JSON or options are malformed.
        KeyError: Required nodes or fields are absent.
        TypeError: Specification is not JSON compatible.
    """  # noqa: DOC503 - Packaged graph and serialization failures propagate.
    mode = str(spec["mode"])
    graph = object_map(
        json.loads(
            (Path(__file__).parent / "workflows" / f"{mode}.api.json").read_text(
                "utf-8"
            )
        )
    )
    settings = object_map(spec["options"])
    prefix, prompt = str(settings["positive_prefix"]), str(spec["prompt"])
    inputs(graph, "4")["text"] = (
        prompt if prefix and prompt.startswith(prefix) else prefix + prompt
    )
    inputs(graph, "5")["text"] = settings["negative_prompt"]
    inputs(graph, "6").update(width=settings["width"], height=settings["height"])
    inputs(graph, "7").update(
        seed=seed,
        steps=settings["steps"],
        cfg=settings["cfg"],
        sampler_name=settings["sampler"],
        scheduler=settings["scheduler"],
        denoise=settings["denoise"],
    )
    aura = next(
        (
            key
            for key, value in graph.items()
            if object_map(value)["class_type"] == "ModelSamplingAuraFlow"
        ),
        "",
    )
    if not aura:
        message = "Packaged workflow is missing its AuraFlow sampling node."
        raise ValueError(message)
    inputs(graph, aura)["shift"] = settings["shift"]
    for index, name in enumerate(names):
        identifier = str(100 + index * 3)
        graph[identifier] = node("LoadImage", {"image": name})
        graph[str(int(identifier) + 1)] = node(
            "ImageScale",
            {
                "image": [identifier, 0],
                "upscale_method": "nearest-exact",
                "width": settings["width"],
                "height": settings["height"],
                "crop": "disabled",
            },
        )
    if mode == "style":
        inputs(graph, "12").update(
            ref_image=["101", 0],
            strength=settings["strength"],
            ip_cfg_scale=settings["cfg"],
        )
    if mode == "character":
        latent: list[object] = ["12", 0]
        for index in range(len(names)):
            identifier = "12" if index == 0 else str(102 + index * 3)
            graph[identifier] = node(
                "AnimaRefEncode",
                {
                    "vae": ["3", 0],
                    "image": [str(101 + index * 3), 0],
                    "target_width": settings["width"],
                    "target_height": settings["height"],
                },
            )
            if index:
                combined = str(200 + index)
                graph[combined] = node(
                    "AnimaRefLatentBatch",
                    {
                        "ref_latent_1": latent,
                        "ref_latent_2": [identifier, 0],
                        "fit_mode": "stretch",
                    },
                )
                latent = [combined, 0]
        inputs(graph, "13").update(
            ref_latent=latent,
            fit_mode="stretch",
            strength=settings["strength"],
            start_percent=settings["start_percent"],
            end_percent=settings["end_percent"],
        )
    if mode == "depth":
        inputs(graph, "11")["image"] = ["101", 0]
        inputs(graph, "13").update(
            strength=settings["strength"],
            start_percent=settings["start_percent"],
            end_percent=settings["end_percent"],
        )
    if names:
        del graph["10"]
    canonical(graph)
    return graph
