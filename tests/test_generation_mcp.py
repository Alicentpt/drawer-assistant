# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise persistent reference-aware jobs, failure boundaries and real MCP stdio."""

import asyncio
import json
import sys
from contextlib import AsyncExitStack
from pathlib import Path

import pytest
from jsonschema import ValidationError
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from drawer_assistant.generation import batches, jobs
from drawer_assistant.generation.graphs import build, inputs
from drawer_assistant.generation.presets import (
    NEGATIVE,
    PREFIX,
    GenerationRequestError,
    array,
    canonical,
    normalize,
)
from drawer_assistant.generation.service import invoke, schemas
from drawer_assistant.network import object_map
from drawer_assistant.store import transaction

FRAME_COUNT = 2
TASK_COUNT = 3
TOOL_COUNT = 7
TEST_STEPS = 40
TEST_CFG = 8
DEPTH_END = 0.6


@pytest.fixture(name="settings")
def profile_settings(tmp_path: Path) -> Path:
    """Prepare an isolated profile and cache, never using live credentials.

    Args:
        tmp_path (Path): Temporary test root.

    Returns:
        Path: Existing plugin settings pointing to a disposable local database.

    Raises:
        OSError: Fixture files cannot be written.
    """  # noqa: DOC502 - Filesystem errors propagate.
    file = tmp_path / "profile/plugins/drawer/settings.json"
    file.parent.mkdir(parents=True)
    file.write_text(
        json.dumps(
            {
                "database": str(tmp_path / "data/test.sqlite3"),
                "comfy_url": "http://127.0.0.1:1",
            }
        ),
        "utf-8",
    )
    return file


@pytest.fixture(name="fake_server")
def server_fixture(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Replace network boundaries while retaining real graphs and persistence.

    Args:
        monkeypatch (pytest.MonkeyPatch): Temporary patch manager.

    Returns:
        list[dict[str, object]]: Graphs submitted by tested operations.
    """
    submitted: list[dict[str, object]] = []

    def api(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
        """Acknowledge submissions and complete their actual expected outputs.

        Args:
            url (str): Mock ComfyUI prompt/history route.
            payload (dict[str, object] | None, default=None): Submitted API graph.

        Returns:
            dict[str, object]: Acknowledgement or successful history.

        Raises:
            AssertionError: An unexpected operation reaches the mock.
        """  # noqa: DOC502 - Assert failures and mapping validation propagate.
        if payload is not None:
            submitted.append(object_map(payload["prompt"]))
            return {"prompt_id": f"abc-{len(submitted):02}"}
        assert "/history/" in url
        return {url.rsplit("/", 1)[1]: {"status": {"completed": True}, "outputs": {}}}

    def download(
        base: str, record: dict[str, object], node_id: str, target: Path
    ) -> str:
        """Write distinguishable output bytes for result and depth preview.

        Args:
            base (str): Mock configured endpoint.
            record (dict[str, object]): Mock successful history.
            node_id (str): Requested output node.
            target (Path): Local destination.

        Returns:
            str: Local output path.

        Raises:
            OSError: Temporary output cannot be written.
        """  # noqa: DOC502 - Filesystem failures propagate.
        del base, record
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\x89PNG\r\n\x1a\n" + node_id.encode())
        return str(target)

    def health(base: str) -> dict[str, object]:
        """Supply a ready GPU-only capability report.

        Args:
            base (str): Unused mock endpoint.

        Returns:
            dict[str, object]: All three modes ready.
        """
        del base
        return {"modes": {mode: {"ready": True} for mode in ("text", "style", "depth")}}

    monkeypatch.setattr(jobs, "api", api)
    monkeypatch.setattr(jobs, "download", download)
    monkeypatch.setattr(jobs, "inspect_server", health)

    def upload(base: str, file: str) -> str:
        """Supply an uploaded filename without mutating a real server.

        Args:
            base (str): Unused mock endpoint.
            file (str): Cached absolute reference path.

        Returns:
            str: Filename consumed by the actual graph builder.
        """
        del base
        return Path(file).name

    monkeypatch.setattr(jobs, "upload", upload)
    return submitted


def test_idempotence_uncertainty(
    settings: Path,
    fake_server: list[dict[str, object]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject changed retries and never duplicate an uncertain remote POST.

    Args:
        settings (Path): Disposable profile settings.
        fake_server (list[dict[str, object]]): Submitted graphs.
        monkeypatch (pytest.MonkeyPatch): Mock network mutation hook.

    Returns:
        None: Same inputs reuse work and uncertain acceptance stays inspectable.

    Raises:
        AssertionError: Duplicate work or unexpected persistence occurs.
        OSError: Fixture data cannot be accessed.
        ValueError: Unexpected request/schema failure occurs.
        RuntimeError: An unexpected backend failure occurs.
        KeyError: Expected fixture data is absent.
        TypeError: Fixture data cannot be serialized.
        sqlite3.Error: Disposable database operations fail.
        jsonschema.ValidationError: Fixture arguments violate the schema.
    """  # noqa: DOC502 - Full service and storage boundaries are exercised.
    request: dict[str, object] = {
        "request_id": "same",
        "prompt": "an adult woman, watercolor",
    }
    result = invoke(settings, "anima_generate", request)
    assert result["state"] == "queued"
    assert invoke(settings, "anima_generate", request)["seed"] == result["seed"]
    assert len(fake_server) == 1
    with pytest.raises(ValueError, match="different inputs"):
        invoke(settings, "anima_generate", {**request, "options": {"cfg": 8}})
    with pytest.raises(ValueError, match="anima_batch"):
        invoke(
            settings, "anima_generate", {**request, "prompt": "a 4x4 grid of panels"}
        )

    def lost_reply(
        url: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Simulate a POST whose acknowledgement was lost after remote acceptance.

        Args:
            url (str): Mock route.
            payload (dict[str, object] | None, default=None): Submitted graph.

        Returns:
            dict[str, object]: Never returned.

        Raises:
            RuntimeError: Always, representing an uncertain HTTP outcome.
        """
        del url, payload
        message = "Lost acknowledgement"
        raise RuntimeError(message)

    monkeypatch.setattr(jobs, "api", lost_reply)
    uncertain = {**request, "request_id": "uncertain"}
    with pytest.raises(RuntimeError, match="Lost acknowledgement"):
        invoke(settings, "anima_generate", uncertain)
    assert invoke(settings, "anima_generate", uncertain)["state"] == "submitting"


def test_reference_graphs(tmp_path: Path) -> None:
    """Ensure every reference is stretched first, windows and prompt overrides apply.

    Args:
        tmp_path (Path): Disposable reference/cache directory.

    Returns:
        None: References, control inputs and overridable presets remain consistent.

    Raises:
        AssertionError: A graph changes model, resizes late or ignores overrides.
        OSError: Temporary references or packaged graphs cannot be accessed.
        ValueError: Normalization or graph loading unexpectedly fails.
        KeyError: A required node is missing.
        TypeError: Fixture data cannot be serialized.
    """  # noqa: DOC502 - Input and graph builders are exercised.
    file = tmp_path / "reference.png"
    file.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    for mode in ("style", "depth"):
        spec = normalize(
            mode,
            {
                "prompt": "pencil drawing",
                "references": [str(file)],
                "options": {"positive_prefix": "", "negative_prompt": "bad anatomy"},
            },
            tmp_path / "cache",
        )
        graph = build(spec, ["uploaded.png"], 42)
        assert inputs(graph, "101")["crop"] == "disabled"
        assert inputs(graph, "101")["width"] == inputs(graph, "6")["width"]
        assert inputs(graph, "4")["text"] == "pencil drawing"
        assert inputs(graph, "5")["text"] == "bad anatomy"
        assert inputs(graph, "1")["unet_name"] == "novaAnimeAM_v20.safetensors"
        if mode == "depth":
            assert inputs(graph, "11")["image"] == ["101", 0]
            assert inputs(graph, "13")["end_percent"] == DEPTH_END
            assert inputs(graph, "19")["images"] == inputs(graph, "13")["image"]
    assert "painting" in PREFIX
    assert "painting" not in NEGATIVE


def test_removed_character(
    settings: Path, fake_server: list[dict[str, object]]
) -> None:
    """Reject removed modes through both entry points while preserving history.

    Args:
        settings (Path): Disposable profile settings and cache.
        fake_server (list[dict[str, object]]): Captured GPU submissions.

    Returns:
        None: Discovery and batches exclude Character; old completed jobs remain.

    Raises:
        AssertionError: Removed work is submitted or historical data is lost.
        OSError: Temporary profile or database cannot be accessed.
        ValueError: Persisted request or result is malformed.
        KeyError: Expected saved data is absent.
        TypeError: Fixture JSON cannot be serialized.
        RuntimeError: Mock transport or database fails.
        sqlite3.Error: SQLite operations fail.
    """  # noqa: DOC502 - Storage and service errors propagate.
    assert "anima_character" not in schemas()
    with pytest.raises(ValidationError):
        invoke(
            settings,
            "anima_batch",
            {
                "request_id": "removed-batch",
                "mode": "character",
                "frames": [{"prompt": "a woman"}, {"prompt": "a garden"}],
            },
        )
    path = Path(str(object_map(json.loads(settings.read_text("utf-8")))["database"]))
    spec: dict[str, object] = {
        "mode": "character",
        "prompt": "old",
        "options": {},
        "references": [],
    }
    with pytest.raises(GenerationRequestError, match="removed"):
        jobs.submit(path, "http://unused", "removed-job", spec)
    jobs.reserve(path, "http://unused", "historical", spec)
    jobs.update(path, "historical", {"state": "completed", "result": canonical({})})
    assert (
        invoke(settings, "generation_status", {"request_id": "historical"})["state"]
        == "completed"
    )
    assert not fake_server


def test_depth_preview(settings: Path, fake_server: list[dict[str, object]]) -> None:
    """Return the saved ControlNet input and its actual denoising window.

    Args:
        settings (Path): Disposable profile settings.
        fake_server (list[dict[str, object]]): Submitted workflow records.

    Returns:
        None: Depth map and final image are separate, and windows stay metadata.

    Raises:
        AssertionError: Preview or effective settings differ from the graph.
        OSError: Temporary files cannot be written.
        ValueError: Fixture inputs or persisted records are invalid.
        TypeError: Fixture data cannot be serialized.
        KeyError: Expected task metadata is absent.
        RuntimeError: Mock transport or database fails.
        sqlite3.Error: SQLite operations fail.
        jsonschema.ValidationError: Fixture request violates its schema.
    """  # noqa: DOC502 - Full service/storage operations are exercised.
    file = settings.parent / "ref.png"
    file.write_bytes(b"\x89PNG\r\n\x1a\nreference")
    invoke(
        settings,
        "anima_depth",
        {
            "request_id": "depth",
            "prompt": "a room, digital painting",
            "references": [str(file)],
            "options": {"end_percent": 0.5},
        },
    )
    result = invoke(settings, "generation_status", {"request_id": "depth"})
    assert Path(str(result["depth_preview"])).read_bytes().endswith(b"19")
    assert Path(str(result["file"])).read_bytes().endswith(b"9")
    assert (
        object_map(result["depth_window"])["end_percent"]
        == inputs(fake_server[0], "13")["end_percent"]
    )


def test_batch_resume(settings: Path, fake_server: list[dict[str, object]]) -> None:
    """Resume frames sequentially with distinct inherited hyperparameters.

    Args:
        settings (Path): Disposable profile settings.
        fake_server (list[dict[str, object]]): Actually submitted graphs.

    Returns:
        None: No second frame starts early, and resumed completed frames are reused.

    Raises:
        AssertionError: Frame overrides, ordering or restart persistence fail.
        OSError: Disposable database cannot be accessed.
        ValueError: Fixture request or result is invalid.
        KeyError: Expected persisted metadata is absent.
        RuntimeError: Mock backend unexpectedly fails.
        TypeError: Fixture data cannot be serialized.
        sqlite3.Error: SQLite operations fail.
        jsonschema.ValidationError: Fixture arguments violate the schema.
    """  # noqa: DOC502 - Persistent batch and service operations are exercised.
    params: dict[str, object] = {
        "request_id": "batch",
        "mode": "text",
        "defaults": {"cfg": 7, "seed": 20},
        "frames": [
            {"prompt": "an adult woman", "options": {"steps": 40}},
            {"prompt": "a forest", "options": {"cfg": 8, "seed": 21}},
        ],
        "assemble": False,
    }
    result = invoke(settings, "anima_batch", params)
    assert result["completed_images"] == 0
    assert len(fake_server) == 1
    path = Path(str(object_map(json.loads(settings.read_text("utf-8")))["database"]))
    result = batches.advance(path, "batch", "")
    assert result["completed_images"] == 1
    assert len(fake_server) == FRAME_COUNT
    result = batches.advance(path, "batch", "")
    assert result["state"] == "completed"
    assert inputs(fake_server[0], "7")["steps"] == TEST_STEPS
    assert inputs(fake_server[1], "7")["cfg"] == TEST_CFG
    assert len(array(result["frames"])) == FRAME_COUNT
    invoke(settings, "anima_batch", params)
    assert len(fake_server) == FRAME_COUNT
    with transaction(path) as database:
        assert (
            database.execute("SELECT count(*) FROM generation_tasks").fetchone()[0]
            == TASK_COUNT
        )


def test_real_stdio(settings: Path) -> None:
    """Use the official client against a real server subprocess and validate errors.

    Args:
        settings (Path): Disposable profile with unreachable loopback endpoint.

    Returns:
        None: Handshake, discovery, schema rejection and contained errors work.

    Raises:
        AssertionError: MCP schemas, errors or framing are invalid.
        OSError: The local MCP subprocess cannot be started.
        RuntimeError: The MCP transport fails unexpectedly.
        ExceptionGroup: MCP transport lifecycle fails.
    """  # noqa: DOC502 - External SDK transport errors propagate.

    async def scenario() -> None:
        """Run an actual MCP client session without any Telegram or GPU mutation.

        Returns:
            None: Seven tools are discovered and invalid input returns an error.

        Raises:
            AssertionError: Discovery or error handling differs from the contract.
            OSError: Subprocess transport cannot be opened.
            RuntimeError: MCP transport fails.
            ExceptionGroup: SDK transport lifecycle fails.
        """  # noqa: DOC502 - External SDK transport errors propagate.
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "drawer_assistant.mcp_generation", "--settings", str(settings)],
        )
        async with AsyncExitStack() as stack:
            reader, writer = await stack.enter_async_context(stdio_client(server))
            session = await stack.enter_async_context(ClientSession(reader, writer))
            await session.initialize()
            discovered = await session.list_tools()
            assert len(discovered.tools) == TOOL_COUNT
            assert "anima_character" not in {tool.name for tool in discovered.tools}
            invalid = await session.call_tool(
                "anima_generate",
                {"request_id": "bad", "prompt": "x", "endpoint": "forbidden"},
            )
            assert invalid.is_error
            offline = await session.call_tool("generation_health", {})
            assert offline.is_error

    asyncio.run(scenario())
