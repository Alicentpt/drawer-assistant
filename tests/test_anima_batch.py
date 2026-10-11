# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Check independent GPU submissions, full-sheet publication and safe batch retries."""

import json
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from drawer_assistant import anima_batch, comfy
from drawer_assistant.network import object_map
from drawer_assistant.plugin import handle
from drawer_assistant.store import orders, transaction

if TYPE_CHECKING:
    from pathlib import Path

FRAME_COUNT = 16
EXPECTED_SCHEMA = 6


def test_sixteen_independent_frames(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create sixteen jobs, assemble in order once and preserve them on retries.

    Args:
        tmp_path (Path): Isolated database, executable and image directory.
        monkeypatch (pytest.MonkeyPatch): Replaces ComfyUI transport and final assembly.

    Returns:
        None: A 4x4 batch requires sixteen independent images and reuses the result.

    Raises:
        AssertionError: Frame count, order, identity, migration or caching is wrong.
        OSError: Fixture, workflow or result files cannot be accessed.
        ValueError: Workflow, stored metadata or response validation fails.
        KeyError: A fixture field is absent.
        RuntimeError: Unexpected generation or assembly failure occurs.
        TypeError: Request data cannot be serialized.
        UnicodeError: Workflow or server data cannot be decoded.
        sqlite3.Error: Test storage operations fail.
    """  # noqa: DOC502 - Delegated storage, generation and assertion failures propagate.
    binary = tmp_path / "magick"
    binary.touch()
    path = tmp_path / "jobs.db"
    identifiers = [f"{index:08x}" for index in range(1, FRAME_COUNT + 1)]
    replies: list[object] = []
    for identifier in identifiers:
        replies.extend(
            [{"system": {"argv": ["--gpu-only"]}}, {"prompt_id": identifier}]
        )
    replies.extend({} for _ in identifiers)
    replies.extend(
        {
            identifier: {
                "status": {
                    "completed": True,
                    "messages": [
                        ["execution_start", {"timestamp": 1000}],
                        ["execution_success", {"timestamp": 11000}],
                    ],
                },
                "outputs": {
                    "9": {
                        "images": [
                            {
                                "filename": identifier + ".png",
                                "subfolder": "",
                                "type": "output",
                            }
                        ]
                    }
                },
            }
        }
        for identifier in identifiers
    )
    remote = Mock(side_effect=replies)
    monkeypatch.setattr(comfy, "api", remote)
    monkeypatch.setattr(
        comfy, "request", Mock(return_value=b"\x89PNG\r\n\x1a\nfixture")
    )
    final = tmp_path / "sheet.png"
    final.write_bytes(b"\x89PNG\r\n\x1a\nfixture")
    assemble = Mock(
        return_value={
            "file": str(final),
            "media": f"MEDIA:{final}",
            "assembly_seconds": 0.1,
            "image_count": FRAME_COUNT,
            "columns": 4,
        }
    )
    monkeypatch.setattr(anima_batch, "sheet", assemble)
    params: dict[str, object] = {
        "action": "start",
        "id": "sixteen",
        "columns": 4,
        "prompts": [
            f"A red car, camera angle {index}. A single scene."
            for index in range(FRAME_COUNT)
        ],
        "labels": [str(index) for index in range(1, FRAME_COUNT + 1)],
    }
    # A version-five database is upgraded without losing an existing order.
    orders(path, {"action": "create", "id": "kept", "title": "Existing order"})
    with transaction(path) as database:
        database.execute("DROP TABLE image_batches")
        database.execute("PRAGMA user_version=5")
    queued = anima_batch.batch(path, "http://test", params, str(binary))
    assert queued["expected_images"] == FRAME_COUNT
    assert queued["completed_images"] == 0
    assert queued["state"] == "queued"
    assemble.assert_not_called()
    status: dict[str, object] = {"action": "status", "id": "sixteen"}
    complete = anima_batch.batch(path, "http://test", status, str(binary))
    assert complete["state"] == "completed"
    assert complete["completed_images"] == FRAME_COUNT
    assert complete["media"] == f"MEDIA:{final}"
    assert assemble.call_args.args[1]["files"] == [
        str(tmp_path / "images" / (identifier + ".png")) for identifier in identifiers
    ]
    assert assemble.call_args.args[1]["columns"] == params["columns"]
    assert assemble.call_args.args[1]["labels"] == params["labels"]
    assert complete["frames"] == [
        {
            "id": f"sixteen-p{index:02}",
            "state": "completed",
            "file": str(tmp_path / "images" / (identifier + ".png")),
            "generation_seconds": "10.00",
        }
        for index, identifier in enumerate(identifiers, 1)
    ]
    assert anima_batch.batch(path, "http://test", status, str(binary)) == complete
    assert anima_batch.batch(path, "http://test", params, str(binary)) == complete
    assert remote.call_count == len(replies)
    assemble.assert_called_once()
    assert orders(path, {"action": "list"})[0]["title"] == "Existing order"
    with transaction(path) as database:
        assert (
            database.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == FRAME_COUNT
        )
        assert database.execute("PRAGMA user_version").fetchone()[0] == EXPECTED_SCHEMA
    with pytest.raises(ValueError, match="different parameters"):
        anima_batch.batch(path, "http://test", {**params, "columns": 2}, str(binary))


def test_uncertain_batch_submission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserve unknown POST acceptance and prevent automatic duplicate submissions.

    Args:
        tmp_path (Path): Isolated executable and job database directory.
        monkeypatch (pytest.MonkeyPatch): Simulates a POST that times out after intent.

    Returns:
        None: Retrying the stable batch id never sends another POST or partial sheet.

    Raises:
        AssertionError: An uncertain job is repeated or reported as a completed batch.
        OSError: Fixture files or database directory cannot be accessed.
        ValueError: Workflow, schema or metadata validation fails.
        KeyError: Required fixture or workflow fields are missing.
        UnicodeError: Workflow data cannot be decoded.
        TypeError: Request data cannot be serialized.
        sqlite3.Error: Fixture database operations fail.
    """  # noqa: DOC502 - Delegated storage and assertion errors propagate.
    binary = tmp_path / "magick"
    binary.touch()
    path = tmp_path / "jobs.db"
    remote = Mock(
        side_effect=[{"system": {"argv": ["--gpu-only"]}}, RuntimeError("uncertain")]
    )
    assemble = Mock()
    monkeypatch.setattr(comfy, "api", remote)
    monkeypatch.setattr(anima_batch, "sheet", assemble)
    params: dict[str, object] = {
        "action": "start",
        "id": "unknown",
        "prompts": ["A red car.", "A blue car."],
    }
    with pytest.raises(RuntimeError, match="uncertain"):
        anima_batch.batch(path, "http://test", params, str(binary))
    result = anima_batch.batch(path, "http://test", params, str(binary))
    assert result["state"] == "needs_attention"
    assert result["completed_images"] == 0
    assert result["frames"] == [
        {
            "id": "unknown-p01",
            "state": "submitting",
            "file": "",
            "generation_seconds": "",
        },
        {"id": "unknown-p02", "state": "not_submitted"},
    ]
    expected_calls = 2
    assert remote.call_count == expected_calls
    assemble.assert_not_called()


@pytest.mark.parametrize(
    ("prompt", "expected"),
    [
        ("4x4 grid, sixteen panels", True),
        ("3 panels side by side", True),
        ("Two women near a grid fence", False),
        ("A single comic-style illustration", False),
    ],
)
def test_multi_panel_redirect(tmp_path: Path, prompt: str, *, expected: bool) -> None:
    """Redirect explicit panel layouts before touching settings or submitting jobs.

    Args:
        tmp_path (Path): Directory without any installed settings or database.
        prompt (str): Grid instruction or a legitimate ordinary scene.
        expected (bool): Whether a multi-panel instruction should be recognized.

    Returns:
        None: Only explicit layouts receive a useful redirect to the batch tool.

    Raises:
        AssertionError: Detection or the safe tool-boundary response is incorrect.
        ValueError: Handler output is malformed JSON.
        KeyError: A response lacks its expected error or hint.
    """  # noqa: DOC502 - JSON parsing and assertion errors propagate.
    assert anima_batch.multipanel(prompt) == expected
    if expected:
        result = object_map(
            json.loads(
                handle(
                    {"action": "start", "id": "one", "prompt": prompt},
                    tool="drawer_anima",
                    settings_file=tmp_path / "absent.json",
                )
            )
        )
        assert result["error"] == "UseAnimaBatch"
        assert "drawer_anima_batch" in str(result["hint"])
        assert not list(tmp_path.iterdir())
