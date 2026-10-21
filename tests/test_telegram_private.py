# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Verify private-only setup never authorizes other users or groups."""

from typing import TYPE_CHECKING

import pytest
import yaml
from dotenv import dotenv_values

from drawer_assistant.setup import configure, mapping
from drawer_assistant.telegram_access import recipient, validate_chat

if TYPE_CHECKING:
    from pathlib import Path


def test_private_setup(tmp_path: Path) -> None:
    """Enable one DM while preserving the profile's unrelated configuration.

    Args:
        tmp_path (Path): Isolated Hermes and project environment tree.

    Returns:
        None: Private policy, matching sender grant and group prohibition persist.

    Raises:
        AssertionError: Setup changes unrelated state or opens another destination.
        OSError: Fixture or configuration cannot be accessed.
        ValueError: Fixture settings or stored configuration are invalid.
        yaml.YAMLError: Configuration is malformed.
    """  # noqa: DOC502 - Setup/parser/assertion failures propagate.
    profile = tmp_path / "profiles/drawer-assistant"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model: {default: retained}\n", "utf-8")
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_BOT_TOKEN=123:fixture\nDRAWER_TELEGRAM_MODE=private\n"
        "DRAWER_TELEGRAM_CHAT_ID=12345\n",
        "utf-8",
    )
    configure(env, profile)
    configure(env, profile)
    config = mapping(yaml.safe_load((profile / "config.yaml").read_text("utf-8")))
    telegram = mapping(config["telegram"])
    assert telegram["allow_from"] == telegram["allowed_chats"] == ["12345"]
    assert telegram["group_allow_from"] == telegram["group_allowed_chats"] == []
    assert telegram["group_policy"] == "disabled"
    assert telegram["dm_policy"] == "allowlist"
    assert telegram["unauthorized_dm_behavior"] == "ignore"
    assert mapping(config["model"])["default"] == "retained"
    assert recipient(profile) == "12345"
    values = dotenv_values(profile / ".env")
    assert values["TELEGRAM_ALLOWED_USERS"] == "12345"
    assert values["DRAWER_TELEGRAM_MODE"] == "private"


@pytest.mark.parametrize(
    ("mode", "chat"),
    [("private", "-123"), ("private", "0"), ("group", "123"), ("other", "123")],
)
def test_explicit_mode_required(mode: str, chat: str) -> None:
    """Reject incompatible ids instead of silently broadening group-only access.

    Args:
        mode (str): Explicit admission mode under test.
        chat (str): Invalid or incompatible numeric destination.

    Returns:
        None: Every incompatible destination is rejected before configuration writes.

    Raises:
        AssertionError: A mismatched mode or id is accepted.
    """  # noqa: DOC502 - Pytest reports missing expected failures.
    with pytest.raises(ValueError, match="DRAWER_TELEGRAM"):
        validate_chat(chat, mode)


def test_private_recipient_matches_user(tmp_path: Path) -> None:
    """Refuse outbound private delivery without the same sole allowed user.

    Args:
        tmp_path (Path): Disposable profile root.

    Returns:
        None: A mismatched user grant cannot redirect reminders or images.

    Raises:
        AssertionError: An unallowlisted private destination is accepted.
        OSError: Fixture environment cannot be written.
    """  # noqa: DOC502 - Filesystem/assertion errors propagate.
    (tmp_path / ".env").write_text(
        "DRAWER_TELEGRAM_MODE=private\nTELEGRAM_ALLOWED_CHATS=12345\n"
        "TELEGRAM_ALLOWED_USERS=99999\n",
        "utf-8",
    )
    with pytest.raises(ValueError, match="same allowed user"):
        recipient(tmp_path)
