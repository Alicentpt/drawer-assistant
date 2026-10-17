# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Normalize generation settings and preserve explicit prompt preset overrides."""

import hashlib
import json
from typing import TYPE_CHECKING, cast

from drawer_assistant.network import object_map
from drawer_assistant.reference_files import EXTENSIONS, load_image

if TYPE_CHECKING:
    from pathlib import Path

PREFIX = (
    "masterpiece, painting, best quality, score_9, score_8, score_7, year 2025, "
    "newest, highres, absurdres, very aesthetic, scenery, "
)
NEGATIVE = (
    "worst quality, low quality, early, score_1, score_2, score_3, graphic, crayon, "
    "graphite, abstract, glitch, deformed, mutated, ugly, disfigured, long body, "
    "bad anatomy, bad hands, missing fingers, extra fingers, extra digits, "
    "fewer digits, cropped, very displeasing, artist name, blurry, jpeg artifacts, "
    "lowres, censor"
)
DEFAULTS: dict[str, object] = {
    "width": 512,
    "height": 512,
    "steps": 45,
    "cfg": 7.0,
    "sampler": "res_multistep",
    "scheduler": "simple",
    "denoise": 1.0,
    "positive_prefix": PREFIX,
    "negative_prompt": NEGATIVE,
    "shift": 3.0,
}
CONTROLS: dict[str, dict[str, object]] = {
    "text": {},
    "style": {"strength": 0.65},
    "character": {"strength": 1.3, "start_percent": 0.0, "end_percent": 1.0},
    "depth": {"strength": 1.0, "start_percent": 0.0, "end_percent": 0.6},
}


def array(value: object) -> list[object]:
    """Validate JSON arrays before narrowing their element type.

    Args:
        value (object): Parsed JSON input.

    Returns:
        list[object]: Validated list with individually unvalidated elements.

    Raises:
        TypeError: Input is not an array.
    """
    if not isinstance(value, list):
        message = "Expected a JSON array."
        raise TypeError(message)
    return cast("list[object]", value)  # Container was validated above.


def canonical(value: object) -> str:
    """Serialize JSON deterministically for immutable requests.

    Args:
        value (object): JSON-compatible request or result.

    Returns:
        str: Sorted JSON without NaN or Infinity.

    Raises:
        TypeError: Input contains non-JSON values.
        ValueError: Input includes non-finite numbers.
    """  # noqa: DOC502 - Serialization errors propagate.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def normalize(mode: str, params: dict[str, object], cache: Path) -> dict[str, object]:
    """Snapshot inputs and merge defaults before storing an immutable request.

    Args:
        mode (str): Allowlisted text, style, character or depth mode.
        params (dict[str, object]): Schema-validated prompt, options and references.
        cache (Path): Persistent input cache outside the source checkout.

    Returns:
        dict[str, object]: Effective options and content-addressed reference files.

    Raises:
        ValueError: Mode, sampling window, dimensions or reference is invalid.
        KeyError: Required request fields are absent.
        OSError: Reference files cannot be read or cached.
        TypeError: Request cannot be serialized.
    """  # noqa: DOC503 - Delegated validation and filesystem errors propagate.
    if mode not in CONTROLS:
        message = "Select text, style, character or depth."
        raise ValueError(message)
    if not str(params["prompt"]).strip():
        message = "Provide a nonempty scene prompt."
        raise ValueError(message)
    supplied = object_map(params.get("options", {}))
    if supplied.keys() - DEFAULTS.keys() - CONTROLS[mode].keys() - {"seed"}:
        message = "Some options do not apply to this generation mode."
        raise ValueError(message)
    options = {**DEFAULTS, **CONTROLS[mode], **supplied}
    if int(str(options["width"])) % 64 or int(str(options["height"])) % 64:
        message = "Image dimensions must be multiples of 64."
        raise ValueError(message)
    if float(str(options.get("start_percent", 0))) >= float(
        str(options.get("end_percent", 1))
    ):
        message = "Require start_percent < end_percent."
        raise ValueError(message)
    refs: list[str] = []
    raw_refs = array(params.get("references", []))
    for source in raw_refs:
        image = load_image({"file": str(source)})
        if image is None:
            message = "Reference file is required."
            raise ValueError(message)
        _, mime, content = image
        cache.mkdir(parents=True, exist_ok=True)
        target = cache / (hashlib.sha256(content).hexdigest() + EXTENSIONS[mime])
        if not target.exists():
            target.write_bytes(content)
        refs.append(str(target.resolve()))
    maximum = 3 if mode == "character" else 1
    if (mode == "text" and refs) or (mode != "text" and not 1 <= len(refs) <= maximum):
        message = (
            "text takes no references; character takes 1..3, other modes exactly 1."
        )
        raise ValueError(message)
    return {
        "mode": mode,
        "prompt": params["prompt"],
        "options": options,
        "references": refs,
    }
