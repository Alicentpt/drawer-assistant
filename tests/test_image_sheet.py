# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Verify image-sheet boundaries, input preservation and native tool dispatch."""

import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from drawer_assistant import image_sheet
from drawer_assistant.network import object_map
from drawer_assistant.plugin import handle


def test_sheet_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Preserve image order and originals while accepting JSON lists and integers.

    Args:
        tmp_path (Path): Isolated image, output and configuration directory.
        monkeypatch (pytest.MonkeyPatch): Replaces the external renderer only.

    Returns:
        None: The handler returns a new PNG, timing and its exact MEDIA marker.

    Raises:
        AssertionError: Arguments, source preservation or the result is incorrect.
        OSError: Fixture or staged output files cannot be accessed.
        ValueError: Result JSON is invalid.
        KeyError: A tool result lacks a required field.
    """  # noqa: DOC502 - Fixture, parsing and assertion failures propagate.
    binary = tmp_path / "magick"
    binary.touch()
    first, second = tmp_path / "first.png", tmp_path / "second.webp"
    first.write_bytes(b"first image")
    second.write_bytes(b"second image")
    calls: list[list[str]] = []

    def render(command: list[str], **options: object) -> None:
        """Write a synthetic successful renderer result and capture the argv.

        Args:
            command (list[str]): Renderer arguments ending in a temporary PNG path.
            **options (object): Process options, including timeout and output capture.

        Returns:
            None: Staged bytes are available for atomic publication.

        Raises:
            AssertionError: Shell use or unbounded process execution is requested.
            OSError: Staged output cannot be written.
        """  # noqa: DOC502 - File writes and assertions can fail.
        assert options == {"check": True, "capture_output": True, "timeout": 60}
        calls.append(command)
        Path(command[-1]).write_bytes(b"\x89PNG\r\n\x1a\nfixture")

    monkeypatch.setattr("drawer_assistant.image_sheet.subprocess.run", render)
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {"database": str(tmp_path / "db"), "imagemagick_binary": str(binary)}
        ),
        "utf-8",
    )
    params: dict[str, object] = {
        "files": [str(second), str(first)],
        "columns": 2,
        "labels": ["Второй кадр", "Первый кадр"],
    }
    result = object_map(
        json.loads(handle(params, tool="drawer_image_sheet", settings_file=settings))
    )
    data = object_map(result["result"])
    output = Path(str(data["file"]))
    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert data["media"] == f"MEDIA:{output}"
    assert data["image_count"] == params["columns"] == data["columns"]
    assert isinstance(data["assembly_seconds"], float)
    assert first.read_bytes() == b"first image"
    assert second.read_bytes() == b"second image"
    command = calls[0]
    assert command.index(f"WEBP:{second}[0]") < command.index(f"PNG:{first}[0]")
    assert "Второй кадр" in command
    assert "512x512+16+16" in command
    assert command[0] == str(binary)
    assert output.parent == tmp_path / "image-sheets"


@pytest.mark.parametrize(
    "params",
    [
        {"files": []},
        {"files": "one.png"},
        {"files": [True]},
        {"files": ["one.png"] * 17},
        {"files": ["relative.png"]},
        {"files": ["https://example.com/image.png"]},
        {"files": ["one.png"], "columns": True},
        {"files": ["one.png"], "columns": "3"},
        {"files": ["one.png"], "cell_size": 4096},
        {"files": ["one.png"], "gap": -1},
        {"files": ["one.png"], "labels": ["a", "b"]},
        {"files": ["one.png"], "labels": ["%[fx:1]"]},
        {"files": ["one.png"], "labels": ["@file"]},
        {"files": ["one.png"], "labels": ["two\nlines"]},
        {"files": ["one.png"], "labels": ["x" * 101]},
        {"files": ["one.png"], "command": "anything"},
    ],
)
def test_sheet_validation(tmp_path: Path, params: dict[str, object]) -> None:
    """Reject malformed requests before launching processes or creating outputs.

    Args:
        tmp_path (Path): Isolated output directory.
        params (dict[str, object]): Malformed list, path, caption or layout request.

    Returns:
        None: Invalid input raises ValueError and does not create an output folder.

    Raises:
        AssertionError: Invalid input is accepted or produces files.
    """  # noqa: DOC502 - Pytest assertions raise on unexpected behavior.
    destination = tmp_path / "outputs"
    with pytest.raises(
        ValueError, match=r"must be|Provide|Use existing|Unknown|Labels"
    ):
        image_sheet.sheet(destination, params)
    assert not destination.exists()


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(1, "magick", stderr="private detail"),
        subprocess.TimeoutExpired("magick", 60),
        None,
    ],
)
def test_sheet_render_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: Exception | None
) -> None:
    """Do not publish a missing, failed or timed-out sheet or expose renderer stderr.

    Args:
        tmp_path (Path): Isolated input, executable and output directory.
        monkeypatch (pytest.MonkeyPatch): Simulates failure or missing output.
        failure (Exception | None): Process exception; None simulates missing output.

    Returns:
        None: Failure leaves no PNG or temporary directory; input remains unchanged.

    Raises:
        AssertionError: Failure is hidden, leaks stderr or leaves a partial output.
        OSError: Fixture files cannot be written or read.
    """  # noqa: DOC502 - Filesystem and assertion failures propagate.
    binary = tmp_path / "magick"
    binary.touch()
    source = tmp_path / "source.png"
    source.write_bytes(b"input bytes")
    monkeypatch.setattr(
        "drawer_assistant.image_sheet.subprocess.run", Mock(side_effect=failure)
    )
    destination = tmp_path / "outputs"
    with pytest.raises(RuntimeError) as caught:
        image_sheet.sheet(destination, {"files": [str(source)]}, str(binary))
    assert "private detail" not in str(caught.value)
    assert not list(destination.iterdir())
    assert source.read_bytes() == b"input bytes"
