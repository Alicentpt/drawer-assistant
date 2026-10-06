# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Verify setup preserves credentials and refuses an unrestricted Telegram bot."""

from typing import TYPE_CHECKING

import pytest
import yaml
from dotenv import dotenv_values

from drawer_assistant.setup import configure, mapping

if TYPE_CHECKING:
    from pathlib import Path


def test_profile_setup(tmp_path: Path) -> None:
    """Keep OAuth/model state while replacing legacy Telegram grants on repeat runs.

    Args:
        tmp_path (Path): Pytest-owned temporary directory, isolated from real Hermes.

    Returns:
        None: Assertions verify the resulting files and unchanged original backup.

    Raises:
        AssertionError: Setup loses unrelated settings or weakens access rules.
        OSError: Temporary files cannot be created or read.
        UnicodeError: A generated file cannot be decoded.
        ValueError: A generated mapping or setup setting is invalid.
        yaml.YAMLError: A generated config cannot be parsed.
    """  # noqa: DOC502 - Tests exercise APIs that raise indirectly.
    profile = tmp_path / "profiles" / "drawer-assistant"
    profile.mkdir(parents=True)
    original = (
        "model: {provider: openai-codex, default: chosen-model}\n"
        "platforms: {telegram: {extra: {allow_from: ['*']}}}\n"
        "gateway: {platforms: {telegram: {extra: {guest_mode: true}}}}\n"
    )
    (profile / "config.yaml").write_text(original, encoding="utf-8")
    (profile / "auth.json").write_text("oauth-state", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123:example\nDRAWER_TELEGRAM_CHAT_ID=-100123\n",
        encoding="utf-8",
    )
    configure(env_file, profile)
    configure(env_file, profile)
    config = mapping(yaml.safe_load((profile / "config.yaml").read_text("utf-8")))
    telegram = mapping(config["telegram"])
    assert telegram["allow_from"] == []
    assert telegram["allowed_chats"] == telegram["group_allowed_chats"] == ["-100123"]
    assert telegram["guest_mode"] is False
    assert telegram["unauthorized_dm_behavior"] == "ignore"
    assert mapping(config["model"])["default"] == "chosen-model"
    assert "telegram" not in mapping(config["platforms"])
    assert "telegram" not in mapping(mapping(config["gateway"])["platforms"])
    assert (profile / "auth.json").read_text("utf-8") == "oauth-state"
    assert (profile / "config.before-drawer.yaml").read_text("utf-8") == original
    credentials = dotenv_values(profile / ".env")
    assert credentials["TELEGRAM_ALLOWED_USERS"] == ""
    # Deliberately invalid fixture credential; no live Telegram token in tests.
    assert credentials["TELEGRAM_BOT_TOKEN"] == "123:example"  # noqa: S105
    assert not (tmp_path / "config.yaml").exists()


@pytest.mark.parametrize("chat", ["", "123", "-100123,-100456", "invite-link", "-0"])
def test_invalid_chat_cannot_enable_bot(tmp_path: Path, chat: str) -> None:
    """Reject missing or ambiguous group restrictions before creating profile files.

    Args:
        tmp_path (Path): Pytest-owned temporary directory.
        chat (str): Invalid value for the single permitted Telegram group.

    Returns:
        None: The invalid setting raises and the profile remains absent.

    Raises:
        AssertionError: Setup accepts the invalid group or writes a profile.
        OSError: The temporary dotenv file cannot be written.
        UnicodeError: The dotenv file cannot be encoded or decoded.
        yaml.YAMLError: Unexpected YAML parsing fails after validation.
    """  # noqa: DOC502 - Includes propagated I/O and assertion failures.
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"TELEGRAM_BOT_TOKEN=123:example\nDRAWER_TELEGRAM_CHAT_ID={chat}\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profiles" / "drawer-assistant"
    with pytest.raises(ValueError, match="DRAWER_TELEGRAM_CHAT_ID"):
        configure(env_file, profile)
    assert not profile.exists()


def test_host_root_is_refused(tmp_path: Path) -> None:
    """Prevent accidentally putting the bot's credentials into the host profile.

    Args:
        tmp_path (Path): Pytest-owned temporary directory posing as a host root.

    Returns:
        None: Setup refuses the host location before any configuration write.

    Raises:
        AssertionError: The host profile is accepted or modified.
        OSError: The temporary dotenv file cannot be written.
        UnicodeError: The dotenv file cannot be encoded or decoded.
        yaml.YAMLError: Unexpected YAML parsing fails after validation.
    """  # noqa: DOC502 - Includes propagated I/O and assertion failures.
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123:example\nDRAWER_TELEGRAM_CHAT_ID=-100123\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="host root"):
        configure(env_file, tmp_path)
    assert not (tmp_path / "config.yaml").exists()
