# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Assemble independent local images with ImageMagick, without a GPU or shell."""

import os
import shutil

# The executable is resolved separately and all arguments are passed without shell.
import subprocess  # nosec B404
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import cast
from uuid import uuid4

MAX_IMAGES = 16
MAX_LABEL = 100
CODERS = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}


def magick_path(configured: str = "") -> str:
    """Resolve ImageMagick even when a running gateway has an older Windows PATH.

    Args:
        configured (str, default=""): Optional absolute path to magick executable.

    Returns:
        str: Absolute executable path, or empty when ImageMagick is unavailable.
    """
    if configured:
        path = Path(configured)
        return str(path) if path.is_absolute() and path.is_file() else ""
    found = shutil.which("magick")
    if found:
        return found
    if os.name == "nt":
        root = Path(os.environ.get("PROGRAMFILES", "C:/Program Files"))
        return next(
            (str(path) for path in sorted(root.glob("ImageMagick-*/magick.exe"))), ""
        )
    return ""


def strings(params: dict[str, object], name: str) -> list[str]:
    """Validate a short JSON list of strings without accepting scalar coercion.

    Args:
        params (dict[str, object]): Model-supplied arguments.
        name (str): List field to read; omitted fields default to an empty list.

    Returns:
        list[str]: Up to sixteen strings in the original order.

    Raises:
        ValueError: The field is not a list of up to sixteen strings.
    """
    value = params.get(name, [])
    if isinstance(value, list):
        # JSON lists are untyped; inspect every element before narrowing.
        items = cast("list[object]", value)
        if len(items) <= MAX_IMAGES and all(isinstance(item, str) for item in items):
            return [str(item) for item in items]
    message = f"{name} must be a list of at most sixteen strings."
    raise ValueError(message)


def integer(
    params: dict[str, object], name: str, default: int, minimum: int, maximum: int
) -> int:
    """Read an integer parameter within a bounded image-layout budget.

    Args:
        params (dict[str, object]): Model-supplied arguments.
        name (str): Numeric field name.
        default (int): Value when the field is omitted.
        minimum (int): Inclusive lower bound.
        maximum (int): Inclusive upper bound.

    Returns:
        int: Validated value; boolean values are rejected.

    Raises:
        ValueError: A supplied value is not an integer within the allowed bounds.
    """
    value = params.get(name, default)
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    ):
        return value
    message = f"{name} must be an integer from {minimum} to {maximum}."
    raise ValueError(message)


def sheet_command(
    params: dict[str, object], executable: str
) -> tuple[list[str], int, int]:
    """Validate sheet inputs and build fixed ImageMagick arguments without output.

    Args:
        params (dict[str, object]): Image paths, labels and layout; see sheet.
        executable (str): Optional absolute configured magick executable.

    Returns:
        tuple[list[str], int, int]: Argument vector, image count and actual columns.

    Raises:
        ValueError: Arguments, input paths, labels or file sizes are invalid.
        RuntimeError: ImageMagick is not installed or configured correctly.
        OSError: Input file metadata cannot be read.
    """  # noqa: DOC503 - File metadata errors propagate.
    if set(params) - {"files", "labels", "columns", "cell_size", "gap"}:
        message = "Unknown image sheet argument."
        raise ValueError(message)
    files = strings(params, "files")
    labels = strings(params, "labels")
    columns = integer(params, "columns", 3, 1, 8)
    size = integer(params, "cell_size", 512, 128, 1024)
    gap = integer(params, "gap", 16, 0, 64)
    if not files or (labels and len(labels) != len(files)):
        message = "Provide images and, optionally, one label per image."
        raise ValueError(message)
    if any(
        len(label) > MAX_LABEL or any(char in label for char in "%@\r\n")
        for label in labels
    ):
        message = "Labels must be single lines up to 100 characters without % or @."
        raise ValueError(message)
    paths = [Path(file) for file in files]
    for path in paths:
        if (
            not path.is_absolute()
            or path.suffix.lower() not in CODERS
            or not path.is_file()
            or not 0 < path.stat().st_size <= 20 * 1024 * 1024
            or any(char in str(path) for char in "[]\r\n")
        ):
            message = "Use existing absolute PNG/JPEG/WebP files, each up to 20 MiB."
            raise ValueError(message)
    binary = magick_path(executable)
    if not binary:
        message = (
            "Install ImageMagick 7 and rerun drawer-setup; check IMAGEMAGICK_BINARY."
        )
        raise RuntimeError(message)
    command = [
        binary,
        "montage",
        "-limit",
        "memory",
        "256MiB",
        "-limit",
        "map",
        "512MiB",
        "-limit",
        "disk",
        "1GiB",
        "-limit",
        "time",
        "45",
        "-background",
        "white",
        "-fill",
        "black",
        "-font",
        "Arial" if os.name == "nt" else "DejaVu-Sans",
        "-pointsize",
        "20",
    ]
    for index, path in enumerate(paths):
        command.extend(
            [
                "(",
                "-label",
                labels[index] if labels else "",
                f"{CODERS[path.suffix.lower()]}:{path}[0]",
                "-auto-orient",
                ")",
            ]
        )
    command.extend(
        [
            "-tile",
            f"{min(columns, len(files))}x",
            "-geometry",
            f"{size}x{size}+{gap}+{gap}",
            "-gravity",
            "center",
        ]
    )
    return command, len(paths), min(columns, len(files))


def sheet(
    directory: Path, params: dict[str, object], executable: str = ""
) -> dict[str, object]:
    """Build a PNG grid in a new file, preserving input images and their order.

    Args:
        directory (Path): Local output directory; created when absent.
        params (dict[str, object]): files: one to sixteen absolute PNG/JPEG/WebP
            paths, each at most 20 MiB; labels: optional equally sized string list,
            at most 100 characters each; columns: 1..8, default 3; cell_size:
            square bounding box 128..1024 pixels, default 512; gap: outer spacing
            0..64 pixels per tile side, default 16. Images fit without cropping.
        executable (str, default=""): Optional absolute ImageMagick binary path.

    Returns:
        dict[str, object]: file and MEDIA marker, input count, columns and measured
        assembly_seconds. Output has a new random filename; originals remain intact.

    Raises:
        ValueError: Arguments, local input paths, labels or file sizes are invalid.
        RuntimeError: ImageMagick is unavailable, rejects an image, times out or
            fails to produce a PNG. Its stderr is not exposed to the model.
        OSError: Input metadata, temporary files, executable or output cannot be
            accessed, or a random output name cannot be generated.
    """  # noqa: DOC503 - Filesystem and randomness errors propagate.
    command, count, columns = sheet_command(params, executable)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"sheet-{uuid4().hex}.png"
    started = perf_counter()
    with TemporaryDirectory(dir=directory) as temporary:
        staged = Path(temporary) / "sheet.png"
        try:
            # Resolved binary, validated local paths and bounded arguments; no shell.
            subprocess.run(  # noqa: S603 # nosec B603
                [*command, str(staged)], check=True, capture_output=True, timeout=60
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            message = "ImageMagick could not assemble the sheet; check images/install."
            raise RuntimeError(message) from exc
        if not staged.is_file() or staged.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
            message = "ImageMagick did not produce a valid PNG output."
            raise RuntimeError(message)
        staged.replace(output)
    return {
        "file": str(output),
        "media": f"MEDIA:{output}",
        "image_count": count,
        "columns": columns,
        "assembly_seconds": round(perf_counter() - started, 2),
    }
