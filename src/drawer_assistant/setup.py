# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Configure the artist's Hermes profile without exposing its bot credentials."""

import argparse
import os
import re
import shutil
from pathlib import Path
from typing import cast

import yaml
from dotenv import dotenv_values, set_key

from .installation import install


def mapping(value: object) -> dict[str, object]:
    """Validate a YAML mapping at the untyped parser boundary.

    Args:
        value (object): Parsed YAML value; None represents an empty mapping.

    Returns:
        dict[str, object]: Mapping with string keys, or a new empty mapping.

    Raises:
        ValueError: The value is not a mapping with string keys.
    """
    if value is None:
        return {}
    # PyYAML has no typed schema; validate its generic mapping before narrowing.
    entries = cast("dict[object, object]", value) if isinstance(value, dict) else {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in entries):
        message = "Hermes config must contain mappings with string keys."
        raise ValueError(message)
    # PyYAML is untyped; the runtime checks above establish this boundary type.
    return cast("dict[str, object]", value)


def configure(env_file: Path, profile: Path) -> None:
    """Restrict Telegram to one group and copy its token into an existing profile.

    Preserve model/OAuth settings and back up the original config once. Replace
    Telegram settings, including legacy duplicate sections. Write access rules
    before enabling the token; a missing group ID never enables unrestricted use.

    Args:
        env_file (Path): UTF-8 dotenv file with bot token and negative group ID.
        profile (Path): Existing Hermes profile directory, never the host root.

    Returns:
        None: Settings are saved without starting a gateway or sending messages.

    Raises:
        ValueError: Required settings, profile location, or YAML mapping is invalid.
        OSError: A configuration file cannot be read, backed up, or written.
        UnicodeError: A configuration file is not valid UTF-8.
        yaml.YAMLError: The existing config is not valid YAML.
    """  # noqa: DOC503 - Includes propagated filesystem/parser exceptions.
    settings = dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")
    token = (settings.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (settings.get("DRAWER_TELEGRAM_CHAT_ID") or "").strip()
    if not token or ":" not in token or "\n" in token or "\r" in token:
        message = "Fill TELEGRAM_BOT_TOKEN in .env with the BotFather token."
        raise ValueError(message)
    if not re.fullmatch(r"-[1-9]\d*", chat):
        message = "Fill DRAWER_TELEGRAM_CHAT_ID with one negative numeric group ID."
        raise ValueError(message)
    if profile.resolve().parent.name != "profiles":
        message = "Use a named Hermes profile under profiles/, not the host root."
        raise ValueError(message)
    config_path = profile / "config.yaml"
    config = mapping(yaml.safe_load(config_path.read_text(encoding="utf-8-sig")))
    gateway = mapping(config.get("gateway"))
    gateway.pop("telegram", None)
    for owner in (config, gateway):
        mapping(owner.get("platforms")).pop("telegram", None)
    gateway["standalone"] = False
    config["gateway"] = gateway
    config["telegram"] = {
        "allowed_chats": [chat],
        "group_allowed_chats": [chat],
        "allow_from": [],
        "dm_policy": "disabled",
        "group_allow_from": ["*"],
        "require_mention": True,
        "guest_mode": False,
        "unauthorized_dm_behavior": "ignore",
        "observe_unmentioned_group_messages": False,
    }
    backup = profile / "config.before-drawer.yaml"
    if not backup.exists():
        shutil.copy2(config_path, backup)
    temporary = profile / "config.drawer.tmp"
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    temporary.replace(config_path)
    # Empty user grants avoid authorizing DMs; allow_from=[] also blocks old pairings.
    credentials = {
        "TELEGRAM_ALLOWED_USERS": "",
        "TELEGRAM_GROUP_ALLOWED_USERS": "",
        "GATEWAY_ALLOWED_USERS": "",
        "TELEGRAM_ALLOW_ALL_USERS": "false",
        "GATEWAY_ALLOW_ALL_USERS": "false",
        "TELEGRAM_ALLOWED_CHATS": chat,
        "TELEGRAM_GROUP_ALLOWED_CHATS": chat,
        "TELEGRAM_GUEST_MODE": "false",
        "TELEGRAM_REQUIRE_MENTION": "true",
        "TELEGRAM_BOT_TOKEN": token,
    }
    for key, value in credentials.items():
        set_key(profile / ".env", key, value)


def main() -> None:
    """Apply .env settings to the standard drawer-assistant profile.

    Returns:
        None: Successful configuration is reported without secret values.

    Raises:
        SystemExit: Help is requested, setup finishes, or an error is reported.
        OSError: The user's home directory cannot be resolved.
        RuntimeError: The user's home directory cannot be determined.
    """  # noqa: DOC502 - argparse and home-directory lookup raise indirectly.
    root = (
        Path(os.environ["LOCALAPPDATA"]) / "hermes"
        if "LOCALAPPDATA" in os.environ
        else Path.home() / ".hermes"
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--profile-dir", type=Path, default=root / "profiles" / "drawer-assistant"
    )
    args = parser.parse_args()
    try:
        configure(args.env_file, args.profile_dir)
        install(args.profile_dir, args.env_file)
    except (
        OSError,
        UnicodeError,
        ValueError,
        TypeError,
        KeyError,
        RuntimeError,
        yaml.YAMLError,
    ) as exc:
        # Parser errors can include YAML contents, so never echo exception text.
        parser.exit(
            1, f"Setup failed ({type(exc).__name__}). Check .env and profile.\n"
        )
    parser.exit(
        0, "Configured: Jessica, tools, reminders, one group. Start host gateway.\n"
    )
