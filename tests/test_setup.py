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
    host_original = "group_sessions_per_user: true\nmodel: {default: host-model}\n"
    (tmp_path / "config.yaml").write_text(host_original, encoding="utf-8")
    original = (
        "model: {provider: openai-codex, default: chosen-model}\n"
        "platforms: {telegram: {extra: {allow_from: ['*']}}}\n"
        "gateway: {platforms: {telegram: {extra: {guest_mode: true}}}}\n"
    )
    (profile / "config.yaml").write_text(original, encoding="utf-8")
    (profile / "auth.json").write_text("oauth-state", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123:example\nDRAWER_TELEGRAM_CHAT_ID=-100123\n"
        "OPENROUTER_API_KEY=unused-fixture\nDRAWER_OPENROUTER_MODEL=\n",
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
    assert telegram["dm_policy"] == "disabled"
    assert telegram["require_mention"] is True
    assert telegram["observe_unmentioned_group_messages"] is False
    assert config["group_sessions_per_user"] is False
    assert config["thread_sessions_per_user"] is False
    assert mapping(config["model"])["default"] == "chosen-model"
    assert "telegram" not in mapping(config["platforms"])
    assert "telegram" not in mapping(mapping(config["gateway"])["platforms"])
    assert (profile / "auth.json").read_text("utf-8") == "oauth-state"
    assert (profile / "config.before-drawer.yaml").read_text("utf-8") == original
    credentials = dotenv_values(profile / ".env")
    assert credentials["TELEGRAM_ALLOWED_USERS"] == ""
    assert "OPENROUTER_API_KEY" not in credentials
    # Deliberately invalid fixture credential; no live Telegram token in tests.
    assert credentials["TELEGRAM_BOT_TOKEN"] == "123:example"  # noqa: S105
    host = mapping(yaml.safe_load((tmp_path / "config.yaml").read_text("utf-8")))
    assert host == {
        "group_sessions_per_user": False,
        "thread_sessions_per_user": False,
        "model": {"default": "host-model"},
    }
    assert (tmp_path / "config.before-drawer-shared-sessions.yaml").read_text(
        "utf-8"
    ) == host_original


def test_openrouter_profile_setup(tmp_path: Path) -> None:
    """Switch dialogue providers without switching images or losing profile state.

    Args:
        tmp_path (Path): Isolated temporary Hermes root.

    Returns:
        None: Repeated setup preserves data and keeps the key outside YAML.

    Raises:
        AssertionError: Credentials leak or unrelated settings change.
        OSError: Temporary files cannot be created or read.
        UnicodeError: Configuration text cannot be encoded or decoded.
        ValueError: Setup or a generated mapping is invalid.
        KeyError: A required setting is missing from generated configuration.
        yaml.YAMLError: Configuration cannot be parsed.
    """  # noqa: DOC502 - Exercises setup's filesystem and parser boundaries.
    profile = tmp_path / "profiles/drawer-assistant"
    profile.mkdir(parents=True)
    original = (
        "model: {provider: openai-codex, default: previous, "
        "base_url: 'https://chatgpt.com/backend-api/codex', "
        "api_mode: codex_responses, key_env: OLD_KEY, api: old-fixture}\n"
        "image_gen: {provider: openai-codex}\n"
        "provider_routing: {sort: price, models: {other/model: {only: [example]}}}\n"
        "terminal: {cwd: artist-notes}\n"
    )
    config_path = profile / "config.yaml"
    config_path.write_text(original, encoding="utf-8")
    (profile / "auth.json").write_text("oauth-state", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123:example\nDRAWER_TELEGRAM_CHAT_ID=-100123\n"
        "DRAWER_OPENROUTER_MODEL=deepseek/deepseek-v4.1-flash\n"
        "OPENROUTER_API_KEY=fixture-router\n",
        encoding="utf-8",
    )
    configure(env_file, profile)
    configure(env_file, profile)
    config = mapping(yaml.safe_load(config_path.read_text("utf-8")))
    assert mapping(config["model"]) == {
        "provider": "openrouter",
        "default": "deepseek/deepseek-v4.1-flash",
        "base_url": "https://openrouter.ai/api/v1",
        "api_mode": "chat_completions",
    }
    assert config["image_gen"] == {"provider": "openai-codex"}
    assert config["terminal"] == {"cwd": "artist-notes"}
    routing = mapping(config["provider_routing"])
    assert routing["sort"] == "price"
    models = mapping(routing["models"])
    assert models["other/model"] == {"only": ["example"]}
    assert models["deepseek/deepseek-v4.1-flash"] == {"require_parameters": True}
    credentials = dotenv_values(profile / ".env")
    assert credentials["OPENROUTER_API_KEY"] == "fixture-router"
    assert "fixture-router" not in config_path.read_text("utf-8")
    assert (profile / "auth.json").read_text("utf-8") == "oauth-state"
    assert (profile / "config.before-drawer.yaml").read_text("utf-8") == original
    assert mapping(yaml.safe_load((tmp_path / "config.yaml").read_text("utf-8"))) == {
        "group_sessions_per_user": False,
        "thread_sessions_per_user": False,
    }


@pytest.mark.parametrize(
    ("model", "key"),
    [("deepseek/model", ""), ("deepseek/model", "two words"), ("https://bad", "x")],
)
def test_invalid_openrouter_preserves_profile(
    tmp_path: Path, model: str, key: str
) -> None:
    """Reject unusable provider settings before any profile files are modified.

    Args:
        tmp_path (Path): Isolated temporary Hermes root.
        model (str): Model ID paired with an invalid model or credential.
        key (str): Credential fixture, never a real API key.

    Returns:
        None: Existing configuration and credentials are byte-for-byte unchanged.

    Raises:
        AssertionError: Invalid settings change or create profile files.
        OSError: Temporary files cannot be created or read.
        UnicodeError: Configuration text cannot be encoded or decoded.
        yaml.YAMLError: Configuration cannot be parsed.
    """  # noqa: DOC502 - Filesystem and parser errors propagate through setup.
    profile = tmp_path / "profiles/drawer-assistant"
    profile.mkdir(parents=True)
    original = "model: {provider: openai-codex}\n"
    (profile / "config.yaml").write_text(original, encoding="utf-8")
    (profile / ".env").write_text("EXISTING=keep\n", encoding="utf-8")
    env_file = tmp_path / ".env"
    env_file.write_text(
        "TELEGRAM_BOT_TOKEN=123:example\nDRAWER_TELEGRAM_CHAT_ID=-100123\n"
        f"DRAWER_OPENROUTER_MODEL='{model}'\nOPENROUTER_API_KEY='{key}'\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="OPENROUTER"):
        configure(env_file, profile)
    assert (profile / "config.yaml").read_text("utf-8") == original
    assert (profile / ".env").read_text("utf-8") == "EXISTING=keep\n"
    assert sorted(path.name for path in profile.iterdir()) == [".env", "config.yaml"]
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
