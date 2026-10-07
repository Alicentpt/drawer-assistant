# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Check model-specific GPU dispatch and private-network HTTP routing."""

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from drawer_assistant import comfy, network
from drawer_assistant.network import object_map
from drawer_assistant.plugin import gpu_tool

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("tool", "expected"),
    [
        (
            "drawer_anima",
            ("novaAnimeAM_v20.safetensors", "qwen_3_06b_base.safetensors", 512),
        ),
        (
            "drawer_zimage",
            (
                "z_image_turbo_w4a8.safetensors",
                "qwen_abliterated_fp8_e4m3fn.safetensors",
                1024,
            ),
        ),
    ],
)
def test_model_binding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    expected: tuple[str, str, int],
) -> None:
    """Keep prompt text intact and bind generation/status to the selected model.

    Args:
        tmp_path (Path): Isolated job database directory.
        monkeypatch (pytest.MonkeyPatch): Replaces the ComfyUI transport.
        tool (str): Registered model-specific tool to exercise.
        expected (tuple[str, str, int]): Diffusion filename, encoder and square size.

    Returns:
        None: Model overrides fail to change the graph or cross-read another tool.

    Raises:
        AssertionError: Model, prompt, preset or job isolation differs.
        ValueError: Fixture arguments, JSON or database schema are invalid.
        KeyError: A packaged node or expected job field is absent.
        OSError: Packaged assets, random source or temporary directory fail.
        UnicodeError: Packaged workflow is not UTF-8.
        sqlite3.Error: Temporary database operations fail.
        RuntimeError: Unexpected transport or GPU preflight failure occurs.
        TypeError: The graph cannot be serialized.
    """  # noqa: DOC502 - Assertions and delegated storage failures propagate.
    remote = Mock(
        side_effect=[
            {"system": {"argv": ["--gpu-only"]}},
            {"prompt_id": "abcd-1234"},
        ]
    )
    monkeypatch.setattr(comfy, "api", remote)
    database = tmp_path / "jobs.db"
    prompt = "  Violet autumn studio.\nTwo adult witches.  "
    result = gpu_tool(
        database,
        "https://artist.example.ts.net",
        tool,
        {"action": "start", "id": "one", "prompt": prompt, "workflow": "wrong"},
    )
    graph = object_map(remote.call_args.args[1]["prompt"])
    assert object_map(object_map(graph["1"])["inputs"])["unet_name"] == expected[0]
    assert object_map(object_map(graph["2"])["inputs"])["clip_name"] == expected[1]
    assert object_map(object_map(graph["6"])["inputs"])["width"] == expected[2]
    assert object_map(object_map(graph["4"])["inputs"])["text"] == prompt
    assert all(
        node["class_type"] != "LoadImage" for node in map(object_map, graph.values())
    )
    if tool == "drawer_anima":
        assert object_map(object_map(graph["5"])["inputs"])["text"] == ""
    assert result["state"] == "queued"
    wrong_tool = "drawer_zimage" if tool == "drawer_anima" else "drawer_anima"
    with pytest.raises(ValueError, match="originally created"):
        gpu_tool(database, "http://test", wrong_tool, {"action": "status", "id": "one"})
    expected_requests = 2  # A mismatched status must not call the server.
    assert remote.call_count == expected_requests


@pytest.mark.parametrize(
    ("host", "direct"),
    [
        ("localhost", True),
        ("artist.example.ts.net", True),
        ("ts.net.example.org", False),
    ],
)
def test_proxy_selection(
    monkeypatch: pytest.MonkeyPatch, host: str, *, direct: bool
) -> None:
    """Bypass HTTP proxies for tailnet hosts without matching lookalike domains.

    Args:
        monkeypatch (pytest.MonkeyPatch): Replaces network I/O and proxy environment.
        host (str): Destination hostname; no actual request leaves the test.
        direct (bool): Whether the request must bypass the configured proxy.

    Returns:
        None: Private destinations bypass proxies and public lookalikes do not.

    Raises:
        AssertionError: Proxy selection or returned response bytes are incorrect.
        ValueError: The fixture URL or request cannot be constructed.
        TypeError: A request payload cannot be serialized.
        RuntimeError: The bounded transport rejects the fixture response.
    """  # noqa: DOC502 - Assertions and delegated transport failures propagate.
    monkeypatch.setenv("https_proxy", "http://test-proxy:8888")
    opener = Mock()
    response = Mock()
    response.read.return_value = b"ok"
    opener.open.return_value.__enter__ = Mock(return_value=response)
    opener.open.return_value.__exit__ = Mock(return_value=False)
    factory = Mock(return_value=opener)
    monkeypatch.setattr(network, "build_opener", factory)
    assert network.request(f"https://{host}/system_stats") == b"ok"
    proxies = factory.call_args.args[0].proxies
    assert (proxies == {}) is direct
    if not direct:
        assert proxies["https"] == "http://test-proxy:8888"
