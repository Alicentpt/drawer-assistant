# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Verify paid generation recovery, model isolation and reference handling offline."""

import json
from email.message import Message
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import pytest

from drawer_assistant import alibaba, network
from drawer_assistant.alibaba_inputs import (
    MODELS,
    PNG,
    InputError,
    endpoint,
    image_input,
    payload,
)
from drawer_assistant.plugin import gpu_tool, handle
from drawer_assistant.store import transaction

OUTPUT_URL = "https://result.oss-cn-shanghai.aliyuncs.com/image.png"


@pytest.fixture(name="profile")
def fixture_profile(tmp_path: Path) -> Path:
    """Create one isolated profile with a deliberately invalid API credential.

    Args:
        tmp_path (Path): Pytest-owned temporary folder.

    Returns:
        Path: Profile with fixture-only dotenv credentials.

    Raises:
        OSError: Fixture cannot be created.
    """  # noqa: DOC502 - File writing errors propagate.
    (tmp_path / ".env").write_text("DASHSCOPE_API_KEY=fixture-only\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("model", MODELS)
def test_references_and_controls(tmp_path: Path, model: str) -> None:
    """Preserve reference order and separate prompts without implicit rewriting.

    Args:
        tmp_path (Path): Isolated local image directory.
        model (str): Each supported fixed model.

    Returns:
        None: The generated request preserves user controls and encodes local data.

    Raises:
        AssertionError: Reference order or controls are lost.
        OSError: Fixture image cannot be written or read.
        ValueError: Image or request validation fails unexpectedly.
        KeyError: A built request field is missing.
    """  # noqa: DOC502 - Test assertions and payload validation can fail.
    image = tmp_path / "референс.png"
    image.write_bytes(PNG + b"fixture")
    params = {
        "prompt": "Keep image 1 pose and image 2 colors.",
        "images": f"{image}\nhttps://example.com/palette.png",
        "seed": "42",
    }
    if model != "qwen-image-2.1-pro":
        params["negative_prompt"] = "blurred hands"
    result = payload(model, params)
    parameters = network.object_map(result["parameters"])
    assert parameters["prompt_extend"] is False
    assert parameters["enable_thinking"] is False
    assert parameters.get("negative_prompt", "") == params.get("negative_prompt", "")
    assert parameters["seed"] == int(params["seed"])
    messages = alibaba.first_object(network.object_map(result["input"])["messages"])
    assert (
        alibaba.first_object(messages["content"])["image"]
        == "data:image/png;base64,iVBORw0KGgpmaXh0dXJl"
    )
    assert str(image) not in json.dumps(result)
    assert "https://example.com/palette.png" in json.dumps(result)


@pytest.mark.parametrize(
    ("model", "changes"),
    [
        ("qwen-image-3.0", {"images": "\n".join(["https://example.com/a.png"] * 4)}),
        ("qwen-image-2.1-pro", {"negative_prompt": "blur"}),
        ("qwen-image-3.0", {"size": "100*100"}),
        ("qwen-image-3.0", {"seed": "2147483648"}),
        ("qwen-image-3.0", {"prompt_extend": "yes"}),
    ],
)
def test_invalid_controls(model: str, changes: dict[str, str]) -> None:
    """Reject unsupported controls before any paid submission.

    Args:
        model (str): Fixed model to validate.
        changes (dict[str, str]): Invalid controls overriding a valid prompt.

    Returns:
        None: Validation rejects the request.

    Raises:
        AssertionError: Unsupported controls are accepted.
        OSError: An unexpected local reference is read.
    """  # noqa: DOC502 - Pytest assertions and reference I/O can fail.
    with pytest.raises(InputError):
        payload(model, {"prompt": "A witch", **changes})


def test_persistent_generation(profile: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Submit once, resume polling, download without credentials and cache the result.

    Args:
        profile (Path): Isolated configured profile.
        monkeypatch (pytest.MonkeyPatch): Replace HTTP without contacting Alibaba.

    Returns:
        None: Restart-safe calls preserve one task, its image and execution duration.

    Raises:
        AssertionError: Submission duplicates or credentials/results leak.
        OSError: Fixture database or result cannot be accessed.
        ValueError: Request, API shape or database schema is invalid.
        KeyError: Expected request or result fields are absent.
        RuntimeError: Unexpected mocked transport failure occurs.
        TypeError: A request cannot be serialized.
        UnicodeError: Fixture credentials cannot be decoded.
        sqlite3.Error: Database operations fail.
    """  # noqa: DOC502 - Assertions and delegated API/storage failures propagate.
    remote = Mock(
        side_effect=[
            {"output": {"task_id": "task-1", "task_status": "PENDING"}},
            {"output": {"task_id": "task-1", "task_status": "RUNNING"}},
            {
                "output": {
                    "task_id": "task-1",
                    "task_status": "SUCCEEDED",
                    "scheduled_time": "2026-10-06 12:00:00.000",
                    "end_time": "2026-10-06 12:00:07.250",
                    "choices": [{"message": {"content": [{"image": OUTPUT_URL}]}}],
                }
            },
        ]
    )
    download = Mock(return_value=PNG + b"fixture")
    monkeypatch.setattr(alibaba, "api", remote)
    monkeypatch.setattr(alibaba, "request", download)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "wrong-profile")
    path = profile / "artist.db"
    params = {"action": "start", "id": "witch", "prompt": "A witch"}
    model = "qwen-image-3.0"
    assert alibaba.run(path, profile, model, params)["state"] == "PENDING"
    alibaba.run(path, profile, model, params)
    assert remote.call_count == 1
    assert remote.call_args.kwargs["headers"]["Authorization"] == "Bearer fixture-only"
    params = {"action": "status", "id": "witch"}
    assert alibaba.run(path, profile, model, params)["state"] == "RUNNING"
    result = alibaba.run(path, profile, model, params)
    assert result["generation_seconds"] == "7.25"
    assert Path(result["file"]).read_bytes() == PNG + b"fixture"
    assert result["media"] == f"MEDIA:{result['file']}"
    assert alibaba.run(path, profile, model, params) == result
    assert download.call_args.kwargs == {}
    expected_calls = 3
    assert remote.call_count == expected_calls
    assert "fixture-only" not in json.dumps(result)
    with pytest.raises(InputError, match="selected model"):
        alibaba.run(path, profile, "qwen-image-3.0-pro", params)
    with pytest.raises(InputError, match="different parameters"):
        alibaba.run(
            path,
            profile,
            model,
            {"action": "start", "id": "witch", "prompt": "Different"},
        )


def test_uncertain_post(profile: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Never charge a second generation after losing the submission response.

    Args:
        profile (Path): Isolated configured profile.
        monkeypatch (pytest.MonkeyPatch): Replace submission with a lost response.

    Returns:
        None: Repeating start or status keeps the unknown acceptance state.

    Raises:
        AssertionError: A second HTTP call occurs.
        OSError: Profile or database access fails.
        ValueError: Fixture arguments or schema are invalid.
        KeyError: Fixture data lacks required fields.
        UnicodeError: Fixture credentials cannot be decoded.
        TypeError: A request cannot be serialized.
        sqlite3.Error: Persisting or retrieving the intent fails.
    """  # noqa: DOC502 - Assertions and storage failures propagate.
    remote = Mock(side_effect=RuntimeError("lost response"))
    monkeypatch.setattr(alibaba, "api", remote)
    path = profile / "artist.db"
    params = {"action": "start", "id": "once", "prompt": "A witch"}
    with pytest.raises(RuntimeError):
        alibaba.run(path, profile, "qwen-image-3.0", params)
    assert alibaba.run(path, profile, "qwen-image-3.0", params)["state"] == "submitting"
    params["action"] = "status"
    assert alibaba.run(path, profile, "qwen-image-3.0", params)["state"] == "submitting"
    assert remote.call_count == 1


def test_validation_and_missing_key(tmp_path: Path) -> None:
    """Refuse secret-like files, untrusted endpoints and unconfigured model calls.

    Args:
        tmp_path (Path): Empty profile directory.

    Returns:
        None: Invalid configuration cannot trigger an outbound request.

    Raises:
        AssertionError: Unsafe inputs pass validation or missing key is unexplained.
        OSError: Fixture files cannot be created or read.
        ValueError: Unexpected fixture JSON or URL syntax is invalid.
        KeyError: Sanitized error does not include a hint.
        UnicodeError: Fixture text cannot be decoded.
    """  # noqa: DOC502 - Assertions and file operations can fail.
    for base in (
        "http://dashscope-intl.aliyuncs.com",
        "https://evil.example",
        "https://dashscope-intl.aliyuncs.com@evil.example",
        "https://dashscope-intl.aliyuncs.com/api/v1",
    ):
        with pytest.raises(InputError):
            endpoint(base)
    assert (
        endpoint("https://workspace.ap-southeast-1.maas.aliyuncs.com/")
        == "https://workspace.ap-southeast-1.maas.aliyuncs.com"
    )
    secret = tmp_path / ".env"
    secret.write_text("EXAMPLE=private", encoding="utf-8")
    with pytest.raises(InputError, match="PNG/JPEG/WebP"):
        image_input(str(secret))
    settings = tmp_path / "plugins/drawer/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"database": str(tmp_path / "db"), "comfy_url": "http://test"}),
        encoding="utf-8",
    )
    reply = json.loads(
        handle(
            {"action": "start", "id": "test", "prompt": "A witch"},
            tool="drawer_qwen_image_3",
            settings_file=settings,
        )
    )
    assert "DASHSCOPE_API_KEY" in reply["hint"]


def test_migration_and_fixed_gpu_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upgrade version two without losing jobs and bind GPU tools to fixed workflows.

    Args:
        tmp_path (Path): Isolated database directory.
        monkeypatch (pytest.MonkeyPatch): Replace ComfyUI network submission.

    Returns:
        None: Migration preserves old jobs and model dispatch cannot be overridden.

    Raises:
        AssertionError: Migration or model isolation fails.
        OSError: Database directory cannot be accessed.
        ValueError: Database schema or tool arguments are invalid.
        KeyError: Required tool fields are absent.
        RuntimeError: Mocked transport fails unexpectedly.
        TypeError: A request cannot be serialized.
        UnicodeError: Workflow cannot be decoded.
        sqlite3.Error: Fixture creation or migration fails.
    """  # noqa: DOC502 - Storage and assertion errors propagate.
    path = tmp_path / "old.db"
    with transaction(path) as database:
        database.execute("DROP TABLE alibaba_jobs")
        database.execute("PRAGMA user_version=2")
        database.execute(
            "INSERT INTO jobs(id,prompt,workflow) VALUES('kept','pose','old')"
        )
    with transaction(path) as database:
        assert (
            database.execute("SELECT prompt FROM jobs WHERE id='kept'").fetchone()[0]
            == "pose"
        )
        version = 4
        assert database.execute("PRAGMA user_version").fetchone()[0] == version
        assert database.execute("SELECT * FROM alibaba_jobs").fetchall() == []
    submit = Mock(return_value={"state": "queued"})
    monkeypatch.setattr("drawer_assistant.plugin.comfy.start", submit)
    gpu_tool(
        path,
        "http://test",
        "drawer_flux_klein",
        {"action": "start", "id": "new", "prompt": "pose", "workflow": "wrong"},
    )
    assert submit.call_args.args[2]["workflow"] == "flux2-klein-abliterated"


@pytest.mark.parametrize("state", ["FAILED", "CANCELED", "UNKNOWN"])
def test_terminal_failures(
    profile: Path, monkeypatch: pytest.MonkeyPatch, state: str
) -> None:
    """Persist terminal errors without returning a media marker or retrying POST.

    Args:
        profile (Path): Isolated configured profile.
        monkeypatch (pytest.MonkeyPatch): Replace HTTP with task lifecycle responses.
        state (str): Provider's terminal failure state.

    Returns:
        None: Repeated status reads the saved error without another network call.

    Raises:
        AssertionError: Failed generation is treated as success or retried.
        OSError: Database or configuration cannot be accessed.
        ValueError: Fixture arguments, API response or schema are invalid.
        KeyError: Expected fields are absent.
        UnicodeError: Fixture credentials cannot be decoded.
        TypeError: A request cannot be serialized.
        RuntimeError: Unexpected transport failure occurs.
        sqlite3.Error: Database operation fails.
    """  # noqa: DOC502 - Assertions and delegated operations can fail.
    remote = Mock(
        side_effect=[
            {"output": {"task_id": "failed-task", "task_status": "PENDING"}},
            {
                "output": {
                    "task_status": state,
                    "code": "DataInspectionFailed",
                    "message": "private provider response",
                }
            },
        ]
    )
    monkeypatch.setattr(alibaba, "api", remote)
    path = profile / "artist.db"
    model = "qwen-image-3.0"
    alibaba.run(
        path, profile, model, {"action": "start", "id": "bad", "prompt": "pose"}
    )
    params = {"action": "status", "id": "bad"}
    result = alibaba.run(path, profile, model, params)
    assert result["state"] == state
    assert result["error_code"] == "DataInspectionFailed"
    assert "media" not in result
    assert "private" not in json.dumps(result)
    assert alibaba.run(path, profile, model, params) == result
    expected_calls = 2
    assert remote.call_count == expected_calls


def test_authenticated_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep authentication in headers and refuse forwarding it through redirects.

    Args:
        monkeypatch (pytest.MonkeyPatch): Replace urllib's opener to avoid the network.

    Returns:
        None: Headers reach the request and the no-redirect handler is installed.

    Raises:
        AssertionError: Authentication or redirect handling is incorrect.
        ValueError: Fixture URL is invalid.
        TypeError: Fixture JSON cannot be serialized.
        RuntimeError: Mocked response exceeds transport limits or fails.
    """  # noqa: DOC502 - Assertions and transport validation can fail.
    opener = Mock()
    opener.open.return_value = BytesIO(b"{}")
    factory = Mock(return_value=opener)
    monkeypatch.setattr(network, "build_opener", factory)
    assert (
        network.request(
            "https://dashscope-intl.aliyuncs.com/api/v1/tasks/test",
            headers={"Authorization": "Bearer fixture-only"},
        )
        == b"{}"
    )
    assert isinstance(factory.call_args.args[1], network.NoRedirect)
    outgoing = opener.open.call_args.args[0]
    assert outgoing.get_header("Authorization") == "Bearer fixture-only"
    assert (
        network.NoRedirect().redirect_request(
            outgoing, BytesIO(), 302, "Found", Message(), "https://evil.example"
        )
        is None
    )
