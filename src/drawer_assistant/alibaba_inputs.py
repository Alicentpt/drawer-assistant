# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Validate Qwen requests and encode local references without exposing credentials."""

import base64
import re
from pathlib import Path
from urllib.parse import urlsplit

from .store import MAX_FIELD

MODELS = {"qwen-image-3.0": 3, "qwen-image-3.0-pro": 3, "qwen-image-2.1-pro": 10}
MAX_IMAGE = 10 * 1024 * 1024
MAX_RATIO = 8
MAX_SEED = 2**31 - 1
PNG = b"\x89PNG\r\n\x1a\n"


class InputError(ValueError):
    """Report a fixed, non-sensitive configuration or tool argument error."""


def endpoint(value: str) -> str:
    """Restrict credential-bearing requests to official Alibaba HTTPS origins.

    Args:
        value (str): Configured origin without an API path.

    Returns:
        str: Validated origin without a trailing slash.

    Raises:
        InputError: Origin contains unsupported host, port, credentials or path.
        ValueError: URL syntax is malformed.
    """  # noqa: DOC503 - URL parsing can raise ValueError.
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    invalid = any(
        (
            parsed.scheme != "https",
            parsed.port not in (None, 443),
            parsed.username is not None,
            parsed.password is not None,
            parsed.path not in ("", "/"),
            bool(parsed.query),
            bool(parsed.fragment),
        )
    )
    if invalid or not (
        host in {"dashscope-intl.aliyuncs.com", "dashscope.aliyuncs.com"}
        or re.fullmatch(
            r"[a-zA-Z0-9-]+\.(ap-southeast-1|ap-northeast-1|us-east-1|"
            r"eu-central-1|cn-beijing|cn-hongkong)\.maas\.aliyuncs\.com",
            host,
        )
    ):
        message = "DASHSCOPE_BASE_URL must be an official Alibaba HTTPS origin."
        raise InputError(message)
    return value.rstrip("/")


def image_input(value: str) -> str:
    """Pass a public HTTPS reference or encode a bounded local PNG/JPEG/WebP.

    Args:
        value (str): Public URL or absolute filename supplied for this generation.

    Returns:
        str: Public HTTPS URL or Base64 data URI; local paths never reach Alibaba.

    Raises:
        InputError: Reference is not HTTPS or an absolute supported image under 10 MiB.
        ValueError: URL syntax is malformed.
        OSError: The local image cannot be opened or read.
    """  # noqa: DOC503 - File and URL parsing failures propagate.
    if value.startswith("https://"):
        parsed = urlsplit(value)
        if parsed.hostname and parsed.username is None and parsed.password is None:
            return value
    path = Path(value)
    if not path.is_absolute():
        message = "References must be public HTTPS URLs or absolute image paths."
        raise InputError(message)
    with path.open("rb") as image:
        data = image.read(MAX_IMAGE + 1)
    mime = (
        "image/png"
        if data.startswith(PNG)
        else "image/jpeg"
        if data.startswith(b"\xff\xd8\xff")
        else "image/webp"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        else ""
    )
    if not mime or len(data) > MAX_IMAGE:
        message = "Local references must be PNG/JPEG/WebP, at most 10 MiB each."
        raise InputError(message)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def payload(model: str, params: dict[str, str]) -> dict[str, object]:
    """Build one generation or reference editing request with explicit controls.

    Args:
        model (str): Fixed model associated with the selected tool.
        params (dict[str, str]): Prompt, newline-separated images, optional negative
            prompt, size, seed and prompt_extend; all tool arguments are strings.

    Returns:
        dict[str, object]: DashScope body producing one image, with no default negative.

    Raises:
        InputError: Model, prompts, size, seed, flags or reference count are invalid.
        ValueError: Numeric or URL parsing fails.
        OSError: A supplied local reference cannot be read.
    """  # noqa: DOC503 - Reference and numeric parsing failures propagate.
    prompt, negative = params.get("prompt", ""), params.get("negative_prompt", "")
    images = [
        item.strip() for item in params.get("images", "").splitlines() if item.strip()
    ]
    if (
        model not in MODELS
        or not prompt.strip()
        or len(prompt) > MAX_FIELD
        or len(negative) > MAX_FIELD
    ):
        message = "Use a supported model and 1-4000 prompt characters; negative <=4000."
        raise InputError(message)
    if len(images) > MODELS[model] or (negative and model == "qwen-image-2.1-pro"):
        message = (
            "Qwen 3.0: up to 3 refs + negative; Qwen 2.1 Pro: up to 10, no negative."
        )
        raise InputError(message)
    size = params.get("size", "1024*1024")
    if not re.fullmatch(r"[1-9]\d{2,4}\*[1-9]\d{2,4}", size):
        message = "Use width*height, e.g. 1024*1024."
        raise InputError(message)
    width, height = (int(item) for item in size.split("*"))
    if (
        not 512**2 <= width * height <= 2048**2
        or not 1 / MAX_RATIO <= width / height <= MAX_RATIO
    ):
        message = "Size needs 512²-2048² pixels, with aspect ratio 1:8 to 8:1."
        raise InputError(message)
    extend = params.get("prompt_extend", "false")
    if extend not in {"true", "false"}:
        message = "prompt_extend must be true or false."
        raise InputError(message)
    parameters: dict[str, object] = {
        "n": 1,
        "size": size,
        "watermark": False,
        "prompt_extend": extend == "true",
        "enable_thinking": extend == "true",
    }
    if negative:
        parameters["negative_prompt"] = negative
    if "seed" in params:
        if not params["seed"].isdigit() or not 0 <= int(params["seed"]) <= MAX_SEED:
            message = "seed must be an integer from 0 to 2147483647."
            raise InputError(message)
        parameters["seed"] = int(params["seed"])
    content = [{"image": image_input(item)} for item in images] + [{"text": prompt}]
    return {
        "model": model,
        "input": {"messages": [{"role": "user", "content": content}]},
        "parameters": parameters,
    }
