# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Protect user text and delivery semantics when applying the readable layout."""

import json
from typing import TYPE_CHECKING
from unittest.mock import Mock

from drawer_assistant import reminders
from drawer_assistant.messages import paragraphs
from drawer_assistant.network import object_map
from drawer_assistant.store import orders

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_brief_words_survive_layout() -> None:
    """Preserve every word and intact reference URL when splitting long display text.

    Returns:
        None: Display spacing and escaping do not truncate the specification.

    Raises:
        AssertionError: The formatter loses words, URLs or Markdown protection.
    """  # noqa: DOC502 - Assertions raise indirectly.
    brief = "Two witches *holding hands*. " * 40 + "https://example.com/ref_a-b.png"
    rendered = paragraphs(brief)
    assert rendered.replace("\\", "").split() == brief.split()
    assert "\n\n" in rendered
    assert "\n\n\n" not in rendered
    assert "https://example\\.com/ref\\_a\\-b\\.png" in rendered
    assert "\\*holding hands\\*" in rendered


def test_html_reminder_delivery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Send readable reminders as escaped HTML without changing their delivery state.

    Args:
        tmp_path (Path): Isolated profile, credentials and database directory.
        monkeypatch (pytest.MonkeyPatch): Replaces Telegram HTTP with an acknowledgment.

    Returns:
        None: The group receives one safe HTML payload and the send is recorded once.

    Raises:
        AssertionError: HTML is unsafe, the target is wrong or delivery repeats.
        OSError: Fixture files or the database cannot be accessed.
        ValueError: Fixture configuration, order or database schema is invalid.
        KeyError: A fixture or configuration field is missing.
        UnicodeError: Fixture text cannot be encoded or decoded.
        sqlite3.Error: Database operations fail.
        OverflowError: Fixture deadline conversion exceeds bounds.
    """  # noqa: DOC502 - Fixture creation and delivery checks can raise.
    path = tmp_path / "orders.db"
    orders(
        path,
        {
            "action": "create",
            "id": "test",
            "title": '<script> & "witches"',
            "due": "2000-01-01T18:00:00+03:00",
            "priority": "3",
        },
    )
    plugin = tmp_path / "plugins/drawer"
    plugin.mkdir(parents=True)
    (plugin / "settings.json").write_text(json.dumps({"database": str(path)}), "utf-8")
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=123:fixture\nTELEGRAM_ALLOWED_CHATS=-100123\n", "utf-8"
    )
    transport = Mock(return_value={"ok": True})
    monkeypatch.setattr(reminders, "api", transport)
    assert reminders.tick(tmp_path) == 1
    payload = object_map(transport.call_args.args[1])
    text = str(payload["text"])
    assert payload["parse_mode"] == "HTML"
    assert payload["chat_id"] == "-100123"
    assert "<script>" not in text
    assert "&lt;script&gt; &amp; &quot;witches&quot;" in text
    assert "\n\n<b>Дедлайн</b>\n01.01.2000 6:00 PM" in text
    assert "3 — высокий" in text
    assert reminders.tick(tmp_path) == 0
    transport.assert_called_once()
