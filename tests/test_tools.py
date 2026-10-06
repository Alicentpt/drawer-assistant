# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise durable orders, reminder retries, references and GPU job boundaries."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

from drawer_assistant import comfy
from drawer_assistant.installation import install_files
from drawer_assistant.network import object_map
from drawer_assistant.plugin import handle
from drawer_assistant.reminders import deliver_due
from drawer_assistant.store import orders, references, utc


def test_order_lifecycle(tmp_path: Path) -> None:
    """Preserve orders across connections and cancel stale reminders after edits.

    Args:
        tmp_path (Path): Isolated pytest data directory.

    Returns:
        None: Persistence, idempotency, rescheduling and completion are verified.

    Raises:
        AssertionError: A persisted value or reminder count is incorrect.
        OSError: Temporary data cannot be created.
        ValueError: Test order validation or schema migration fails.
        KeyError: A fixture field or timezone is missing.
        sqlite3.Error: Temporary database operations fail.
        OverflowError: A fixture timestamp cannot be converted.
    """  # noqa: DOC502 - Tests exercise APIs that raise indirectly.
    database = tmp_path / "orders.db"
    params = {
        "action": "create",
        "id": "portrait",
        "title": "Portrait",
        "due": "2026-10-07T12:00:00+02:00",
    }
    first = orders(database, params)
    assert orders(database, params) == first
    assert len(orders(database, {"action": "list"})) == 1
    messages: list[str] = []
    now = datetime(2026, 10, 7, 10, tzinfo=UTC)
    assert deliver_due(database, messages.append, now - timedelta(seconds=1)) == 0
    orders(
        database,
        {"action": "update", "id": "portrait", "due": "2026-10-08T12:00:00+02:00"},
    )
    assert deliver_due(database, messages.append, now) == 0
    assert deliver_due(database, messages.append, now + timedelta(days=1)) == 1
    assert deliver_due(database, messages.append, now + timedelta(days=1)) == 0
    assert len(messages) == 1
    orders(database, {"action": "update", "id": "portrait", "status": "done"})
    assert deliver_due(database, messages.append, now + timedelta(days=2)) == 0
    with pytest.raises(ValueError, match="already exists"):
        orders(database, {**params, "title": "Different order"})


def test_retry_and_clear(tmp_path: Path) -> None:
    """Persist failed delivery backoff, acknowledge once, and honor cleared dates.

    Args:
        tmp_path (Path): Isolated pytest data directory.

    Returns:
        None: A failed send is retried only when due and cleared reminders stay off.

    Raises:
        AssertionError: Delivery accounting or retry timing is incorrect.
        OSError: Temporary database directory cannot be created.
        ValueError: A fixture or database schema is invalid.
        KeyError: A fixture field or timezone is missing.
        sqlite3.Error: Temporary database operations fail.
        OverflowError: A fixture timestamp cannot be converted.
    """  # noqa: DOC502 - Includes delegated storage and clock errors.
    database = tmp_path / "orders.db"
    orders(
        database,
        {
            "action": "create",
            "id": "test",
            "title": "Test",
            "due": "2026-10-07T12:00:00+02:00",
        },
    )

    def offline(text: str) -> None:
        """Simulate an unavailable Telegram endpoint.

        Args:
            text (str): Reminder body, intentionally unused.

        Returns:
            None: Never returned because this sender always fails.

        Raises:
            RuntimeError: The simulated endpoint is offline.
        """
        del text
        message = "offline"
        raise RuntimeError(message)

    now = datetime(2026, 10, 7, 10, tzinfo=UTC)
    assert deliver_due(database, offline, now) == 0
    record = orders(database, {"action": "list"})[0]
    assert record["last_error"] == "delivery_failed"
    assert record["attempts"] == "1"
    messages: list[str] = []
    assert deliver_due(database, messages.append, now + timedelta(seconds=59)) == 0
    assert deliver_due(database, messages.append, now + timedelta(minutes=1)) == 1
    orders(database, {"action": "update", "id": "test", "due": ""})
    assert deliver_due(database, messages.append, now + timedelta(days=1)) == 0


@pytest.mark.parametrize(
    "value", ["2026-10-07T12:00:00", "2026-10-07T12:00:00+03:00", "not a date"]
)
def test_reject_ambiguous_time(value: str) -> None:
    """Reject naive timestamps and offsets that contradict the configured zone.

    Args:
        value (str): Invalid local timestamp for Europe/Kaliningrad.

    Returns:
        None: The invalid timestamp raises instead of silently changing the time.

    Raises:
        AssertionError: An invalid timestamp is accepted.
        KeyError: Timezone data is unavailable.
        OverflowError: The fixture timestamp exceeds datetime limits.
    """  # noqa: DOC502 - Test assertions and timezone errors propagate.
    with pytest.raises(ValueError, match=r"ISO|isoformat"):
        utc(value, "Europe/Kaliningrad")


def test_references_and_error_boundary(tmp_path: Path) -> None:
    """Associate source links with existing orders and sanitize malformed tool calls.

    Args:
        tmp_path (Path): Isolated pytest directory for SQLite and settings.

    Returns:
        None: Links remain durable and invalid calls return a tool error.

    Raises:
        AssertionError: Linking or error handling behaves incorrectly.
        OSError: Fixture files cannot be accessed.
        ValueError: A fixture argument or schema is invalid.
        KeyError: A fixture field or timezone is missing.
        sqlite3.Error: Temporary database operations fail.
        OverflowError: Timestamp normalization fails.
    """  # noqa: DOC502 - Includes delegated test and storage errors.
    database = tmp_path / "orders.db"
    params = {
        "action": "save",
        "id": "pose",
        "title": "Pose",
        "url": "https://example.com/pose",
        "order_id": "test",
    }
    with pytest.raises(KeyError):
        references(database, params)
    orders(database, {"action": "create", "id": "test", "title": "Test"})
    references(database, params)
    assert (
        references(database, {"action": "list", "order_id": "test"})[0]["url"]
        == params["url"]
    )
    output = handle(
        {"action": 123}, tool="drawer_orders", settings_file=tmp_path / "missing"
    )
    assert "error" in object_map(json.loads(output))


def test_comfy_job_retry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Do not re-submit a request after an uncertain HTTP POST or process restart.

    Args:
        tmp_path (Path): Isolated database directory.
        monkeypatch (pytest.MonkeyPatch): Pytest-owned transport replacement.

    Returns:
        None: The job survives the failed POST and no duplicate is submitted.

    Raises:
        AssertionError: A duplicate submission or invalid graph is observed.
        OSError: Database or packaged workflow cannot be read.
        ValueError: Test arguments or schema are invalid.
        KeyError: Packaged nodes or test parameters are missing.
        UnicodeError: Packaged workflow cannot be decoded.
        sqlite3.Error: Temporary database operations fail.
        TypeError: The fixture graph cannot be serialized.
        RuntimeError: An unexpected request fails outside the expected POST.
    """  # noqa: DOC502 - Includes delegated graph and persistence errors.
    posts: list[dict[str, object]] = []

    def fake_api(
        url: str, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        """Return GPU-only settings but simulate a lost submission acknowledgment.

        Args:
            url (str): API route under test.
            payload (dict[str, object] | None, default=None): Submitted JSON or None.

        Returns:
            dict[str, object]: GPU-only settings for the preflight request.

        Raises:
            RuntimeError: Every non-preflight request loses its response.
        """
        if url.endswith("/system_stats"):
            return {"system": {"argv": ["main.py", "--gpu-only"]}}
        posts.append(payload or {})
        message = "Response lost after acceptance"
        raise RuntimeError(message)

    monkeypatch.setattr(comfy, "api", fake_api)
    database = tmp_path / "orders.db"
    params = {"id": "art-1", "prompt": "A blue coat reference"}
    with pytest.raises(RuntimeError, match="Response lost"):
        comfy.start(database, "http://test", params)
    assert comfy.start(database, "http://test", params)["state"] == "submitting"
    assert comfy.status(database, "http://test", "art-1")["state"] == "submitting"
    assert len(posts) == 1


def test_install_preserves_data(tmp_path: Path) -> None:
    """Install persona and artist tools without erasing data or the original persona.

    Args:
        tmp_path (Path): Isolated directory standing in for the Hermes profile.

    Returns:
        None: Installation remains repeatable and keeps existing profile settings.

    Raises:
        AssertionError: Reinstallation loses the backup or enables administration.
        OSError: Fixture or installed files cannot be written.
        ValueError: Generated configuration or JSON is invalid.
        KeyError: A generated setting or timezone is absent.
        UnicodeError: Text files cannot be encoded or decoded.
        yaml.YAMLError: The generated profile configuration cannot be parsed.
        shutil.Error: Plugin files cannot be copied.
    """  # noqa: DOC502 - Installer and test failures propagate.
    (tmp_path / "config.yaml").write_text(
        "platform_toolsets:\n  telegram: [image_gen]\n", "utf-8"
    )
    (tmp_path / "SOUL.md").write_text("original", "utf-8")
    env = tmp_path / ".env"
    env.write_text("DRAWER_TIMEZONE=Europe/Kaliningrad\n", "utf-8")
    install_files(tmp_path, env)
    install_files(tmp_path, env)
    assert (tmp_path / "SOUL.before-drawer.md").read_text("utf-8") == "original"
    assert "Джесс" in (tmp_path / "SOUL.md").read_text("utf-8")
    settings = object_map(
        json.loads((tmp_path / "plugins/drawer/settings.json").read_text("utf-8"))
    )
    assert Path(str(settings["database"])).parent == tmp_path / "drawer-data"
    configuration = object_map(
        yaml.safe_load((tmp_path / "config.yaml").read_text("utf-8"))
    )
    assert object_map(configuration["platform_toolsets"])["telegram"] == [
        "drawer",
        "image_gen",
        "vision",
        "web",
        "clarify",
        "memory",
    ]


@pytest.mark.parametrize("gpu_only", [True, False])
def test_comfy_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, gpu_only: bool
) -> None:
    """Require GPU-only mode and cache completed media without submitting twice.

    Args:
        tmp_path (Path): Isolated database and image directory.
        monkeypatch (pytest.MonkeyPatch): Replaces HTTP transport with API fixtures.
        gpu_only (bool): Whether the mock server enables the required GPU mode.

    Returns:
        None: Unsafe startup is rejected, or a completed job downloads exactly once.

    Raises:
        AssertionError: GPU enforcement, media caching or submission count is wrong.
        OSError: Temporary files or packaged workflows cannot be accessed.
        ValueError: Fixture JSON, workflow or schema is invalid.
        KeyError: A fixture field or packaged node is absent.
        UnicodeError: A fixture or packaged workflow cannot be decoded.
        sqlite3.Error: Temporary database operations fail.
        RuntimeError: An unexpected API failure occurs.
        TypeError: A fixture graph cannot be serialized.
    """  # noqa: DOC502 - Test assertions and API/storage failures propagate.
    remote = Mock(
        side_effect=[
            {"system": {"argv": ["--gpu-only"] if gpu_only else ["--lowvram"]}},
            {"prompt_id": "abcd-1234"},
            {
                "abcd-1234": {
                    "status": {"completed": True},
                    "outputs": {
                        "9": {
                            "images": [
                                {
                                    "filename": "result.png",
                                    "subfolder": "",
                                    "type": "output",
                                }
                            ]
                        }
                    },
                }
            },
        ]
    )
    png = b"\x89PNG\r\n\x1a\nfixture"
    download = Mock(return_value=png)
    monkeypatch.setattr(comfy, "api", remote)
    monkeypatch.setattr(comfy, "request", download)
    database = tmp_path / "jobs.db"
    params = {"id": "result", "prompt": "Blue coat"}
    if not gpu_only:
        with pytest.raises(RuntimeError, match="gpu-only"):
            comfy.start(database, "http://test", params)
        assert remote.call_count == 1
        return
    comfy.start(database, "http://test", params)
    comfy.start(database, "http://test", params)
    result = comfy.status(database, "http://test", "result")
    assert Path(result["file"]).read_bytes() == png
    assert result["media"] == f"MEDIA:{result['file']}"
    assert comfy.status(database, "http://test", "result") == result
    expected_requests = 3  # One preflight, one submission and one history lookup.
    assert remote.call_count == expected_requests
    assert download.call_count == 1
