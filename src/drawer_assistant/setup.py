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

    Preserve OAuth, optionally select OpenRouter, and back up the config once. Share
    group sessions on the host and profile; replace legacy Telegram settings.
    Write access rules before enabling the token; a missing group ID never
    enables unrestricted use.

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
    credential = configure_openrouter(config, settings)
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
    configure_shared_sessions(profile.parent.parent)
    config.update(group_sessions_per_user=False, thread_sessions_per_user=False)
    backup = profile / "config.before-drawer.yaml"
    if not backup.exists():
        shutil.copy2(config_path, backup)
    temporary = profile / "config.drawer.tmp"
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    if credential:
        set_key(profile / ".env", "OPENROUTER_API_KEY", credential)
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
    for key, credential in credentials.items():
        set_key(profile / ".env", key, credential)


def configure_shared_sessions(host: Path) -> None:
    """Share group conversations through the host's native Hermes session routing.

    The multiplexed gateway reads these flags from the host, not each profile.
    Preserve unrelated settings and back up the original once. No histories,
    credentials, Telegram admission rules or model selections are modified.

    Args:
        host (Path): Existing Hermes host directory containing profiles/.

    Returns:
        None: Host routing shares each group/topic across its participants.

    Raises:
        OSError: Configuration cannot be read, backed up or replaced.
        UnicodeError: Configuration text is not UTF-8.
        ValueError: The existing configuration is not a mapping.
        yaml.YAMLError: The existing configuration is malformed YAML.
    """  # noqa: DOC502 - File, mapping and parser failures propagate.
    filename = host / "config.yaml"
    config = mapping(
        yaml.safe_load(filename.read_text("utf-8-sig")) if filename.exists() else {}
    )
    backup = host / "config.before-drawer-shared-sessions.yaml"
    if filename.exists() and not backup.exists():
        shutil.copy2(filename, backup)
    config.update(group_sessions_per_user=False, thread_sessions_per_user=False)
    temporary = filename.with_suffix(".drawer.tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    temporary.replace(filename)


def configure_openrouter(
    config: dict[str, object], settings: dict[str, str | None]
) -> str:
    """Select Hermes' native OpenRouter provider only with an explicit model and key.

    Mutate the in-memory config after validation; leave image tools, OAuth and
    unrelated settings intact. Credentials are returned separately for dotenv.

    Args:
        config (dict[str, object]): Parsed Hermes profile configuration to update.
        settings (dict[str, str | None]): Project dotenv values; an empty
            DRAWER_OPENROUTER_MODEL leaves the current model and credentials intact.

    Returns:
        str: Validated API key to save in the profile, or empty when not selected.

    Raises:
        ValueError: Model ID, API key or an existing configuration mapping is invalid.
    """
    model_id = (settings.get("DRAWER_OPENROUTER_MODEL") or "").strip()
    if not model_id:
        return ""
    key = (settings.get("OPENROUTER_API_KEY") or "").strip()
    if not re.fullmatch(r"[\w.-]+/[\w.:-]+", model_id):
        message = "DRAWER_OPENROUTER_MODEL must be an OpenRouter author/model ID."
        raise ValueError(message)
    if not key or any(character.isspace() for character in key):
        message = "Fill OPENROUTER_API_KEY before selecting an OpenRouter model."
        raise ValueError(message)
    model = mapping(config.get("model")).copy()
    routing = mapping(config.get("provider_routing")).copy()
    models = mapping(routing.get("models")).copy()
    route = mapping(models.get(model_id)).copy()
    # Native Hermes clears stale custom-provider credential pointers on switches.
    for field in ("api_key", "api", "key_env", "api_key_env"):
        model.pop(field, None)
    model.update(
        provider="openrouter",
        default=model_id,
        base_url="https://openrouter.ai/api/v1",
        api_mode="chat_completions",
    )
    route["require_parameters"] = True
    models[model_id] = route
    routing["models"] = models
    config.update(model=model, provider_routing=routing)
    return key


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
