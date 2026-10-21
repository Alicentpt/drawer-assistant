# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Select one explicit Telegram group or one allowlisted private recipient."""

import re
from typing import TYPE_CHECKING

from dotenv import dotenv_values

if TYPE_CHECKING:
    from pathlib import Path


def validate_chat(chat: str, mode: str = "group") -> str:
    """Require the numeric destination appropriate to the explicitly chosen mode.

    Args:
        chat (str): Negative group id, or positive private user/chat id.
        mode (str, default="group"): Explicit group or private admission mode.

    Returns:
        str: Validated id; existing installations retain group-only defaults.

    Raises:
        ValueError: Mode or DRAWER_TELEGRAM_CHAT_ID is missing or incompatible.
    """
    pattern = {"group": r"-[1-9]\d*", "private": r"[1-9]\d*"}.get(mode)
    if pattern is None or not re.fullmatch(pattern, chat):
        message = (
            "Set DRAWER_TELEGRAM_MODE to group/private and DRAWER_TELEGRAM_CHAT_ID "
            "to one matching numeric id: negative for group, positive for private."
        )
        raise ValueError(message)
    return chat


def policy(chat: str) -> dict[str, object]:
    """Construct native Hermes rules for a validated single destination.

    Args:
        chat (str): Id previously checked by validate_chat.

    Returns:
        dict[str, object]: Private allowlist or group-only policy; other DMs ignore.
    """
    group = chat.startswith("-")
    return {
        "allowed_chats": [chat],
        "group_allowed_chats": [chat] if group else [],
        "allow_from": [] if group else [chat],
        "dm_policy": "disabled" if group else "allowlist",
        "group_allow_from": ["*"] if group else [],
        "group_policy": "open" if group else "disabled",
        "require_mention": group,
        "guest_mode": False,
        "unauthorized_dm_behavior": "ignore",
        "observe_unmentioned_group_messages": False,
    }


def recipient(profile: Path) -> str:
    """Read outbound destination and check matching private-user admission.

    Args:
        profile (Path): Owning Hermes profile, never a model-supplied path.

    Returns:
        str: One permitted id shared by reminders and generation delivery.

    Raises:
        OSError: The profile environment cannot be read.
        ValueError: Mode/id is invalid or private-user grants do not match.
    """  # noqa: DOC503 - Profile reading errors propagate.
    values = dotenv_values(profile / ".env", interpolate=False, encoding="utf-8-sig")
    chat = validate_chat(
        values.get("TELEGRAM_ALLOWED_CHATS") or "",
        values.get("DRAWER_TELEGRAM_MODE") or "group",
    )
    if not chat.startswith("-") and values.get("TELEGRAM_ALLOWED_USERS") != chat:
        message = "Private Telegram delivery requires exactly the same allowed user."
        raise ValueError(message)
    return chat
