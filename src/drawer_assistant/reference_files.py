# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Import bounded image bytes and materialize disposable copies of SQLite assets."""

import hashlib
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .network import request

MAX_IMAGE = 20 * 1024 * 1024
EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def image_type(data: bytes) -> str:
    """Recognize supported image signatures without decoding or transforming bytes.

    Args:
        data (bytes): Complete downloaded or locally read file, at most 20 MiB.

    Returns:
        str: MIME type selected from PNG, JPEG, WebP and GIF.

    Raises:
        ValueError: Size exceeds 20 MiB or no supported signature is present.
    """
    mime = (
        "image/png"
        if data.startswith(b"\x89PNG\r\n\x1a\n")
        else "image/jpeg"
        if data.startswith(b"\xff\xd8\xff")
        else "image/webp"
        if data.startswith(b"RIFF") and data[8:12] == b"WEBP"
        else "image/gif"
        if data.startswith((b"GIF87a", b"GIF89a"))
        else ""
    )
    if not mime or len(data) > MAX_IMAGE:
        message = "Reference must be PNG/JPEG/WebP/GIF, at most 20 MiB."
        raise ValueError(message)
    return mime


def load_image(params: dict[str, str]) -> tuple[str, str, bytes] | None:
    """Copy a Telegram cache file or explicitly selected image URL into memory.

    Args:
        params (dict[str, str]): Optional file (absolute path) or image_url (HTTP/S).
            Both omitted means metadata only; supplying both is rejected.

    Returns:
        tuple[str, str, bytes] | None: Filename, detected MIME and exact bytes;
            None when no image source was supplied.

    Raises:
        ValueError: Sources conflict, path is relative, or image/URL is unsupported.
        OSError: The local image cannot be read.
        RuntimeError: Download fails or exceeds the transport's response limit.
    """  # noqa: DOC503 - Transport and filesystem failures propagate.
    file, url = params.get("file", ""), params.get("image_url", "")
    if not file and not url:
        return None
    if file and url:
        message = "Supply either file or image_url, not both."
        raise ValueError(message)
    if file:
        path = Path(file)
        if not path.is_absolute() or not path.is_file():
            message = "file must name an existing absolute image file."
            raise ValueError(message)
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE + 1)
        filename = path.name
    else:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            message = "image_url must be an HTTP(S) image URL."
            raise ValueError(message)
        data = request(url)
        filename = Path(parsed.path).name or "image"
    return filename[:255], image_type(data), data


def materialize(directory: Path, data: bytes, digest: str, mime: str) -> Path:
    """Atomically restore an expendable image copy from its authoritative BLOB.

    Args:
        directory (Path): Export directory beside the database, created if absent.
        data (bytes): Exact bytes read from SQLite.
        digest (str): Expected SHA-256; also supplies the safe output basename.
        mime (str): Stored MIME type determining the output extension.

    Returns:
        Path: Absolute path suitable for Hermes MEDIA or generation input.

    Raises:
        ValueError: Stored bytes fail their SHA-256 integrity check.
        KeyError: Stored MIME is unsupported.
        OSError: The directory, temporary file or destination cannot be written.
    """  # noqa: DOC503 - File operations and MIME lookup can fail.
    if hashlib.sha256(data).hexdigest() != digest:
        message = "Stored reference failed its SHA-256 integrity check."
        raise ValueError(message)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / (digest + EXTENSIONS[mime])
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(data)
        except OSError:
            stream.close()
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output.resolve()
