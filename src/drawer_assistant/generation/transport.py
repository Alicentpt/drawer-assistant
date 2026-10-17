# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Upload bounded reference bytes and download fixed ComfyUI output nodes."""

import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

from drawer_assistant.network import object_map, request
from drawer_assistant.reference_files import MAX_IMAGE, image_type

from .presets import array


def upload(base: str, file: str) -> str:
    """Upload a content-addressed input without overwriting another image.

    Args:
        base (str): Operator-configured ComfyUI base URL.
        file (str): Existing cached absolute reference filename.

    Returns:
        str: ComfyUI-relative input filename accepted by LoadImage.

    Raises:
        OSError: The cached reference cannot be read.
        ValueError: Image or server response is invalid.
        RuntimeError: Upload fails; no generation is submitted here.
        KeyError: Response lacks its uploaded filename.
    """  # noqa: DOC503 - File, parser and transport failures propagate.
    path = Path(file)
    with path.open("rb") as source:
        content = source.read(MAX_IMAGE + 1)
    mime = image_type(content)
    digest = hashlib.sha256(content).hexdigest()
    boundary = "drawer-" + digest[:24]
    filename = digest + path.suffix
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; '
        f'filename="{filename}"\r\nContent-Type: {mime}\r\n\r\n'.encode()
        + content
        + f"\r\n--{boundary}--\r\n".encode()
    )
    response = object_map(
        json.loads(
            request(
                base + "/upload/image",
                body,
                headers={"Content-Type": "multipart/form-data; boundary=" + boundary},
            )
        )
    )
    name = str(response["name"])
    folder = str(response.get("subfolder", ""))
    if "/" in name or "\\" in name or name in {"", ".", ".."} or folder:
        message = "Unexpected ComfyUI upload filename or folder."
        raise ValueError(message)
    return name


def download(base: str, record: dict[str, object], node_id: str, target: Path) -> str:
    """Download exactly the image consumed by the named output node.

    Args:
        base (str): Configured ComfyUI base URL.
        record (dict[str, object]): Completed history record.
        node_id (str): Fixed output node 9 or depth preview node 19.
        target (Path): Persistent local output filename.

    Returns:
        str: Absolute PNG filename written atomically.

    Raises:
        ValueError: History output or PNG bytes are invalid.
        KeyError: Expected output metadata is absent.
        RuntimeError: Download fails.
        OSError: Output cannot be written.
    """  # noqa: DOC503 - Parser, transport and filesystem failures propagate.
    images = array(object_map(object_map(record["outputs"])[node_id])["images"])
    if not images:
        message = "Completed workflow has no expected output image."
        raise ValueError(message)
    image = object_map(images[0])
    query = urlencode(
        {key: str(image[key]) for key in ("filename", "subfolder", "type")}
    )
    content = request(base + "/view?" + query)
    if image_type(content) != "image/png":
        message = "Workflow must return PNG."
        raise ValueError(message)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".tmp")
    temporary.write_bytes(content)
    temporary.replace(target)
    return str(target.resolve())
