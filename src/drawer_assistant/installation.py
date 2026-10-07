# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Install the persona, native tools and a no-agent reminder tick into Hermes."""

import json
import os
import shutil

# Only fixed Hermes CLI commands are executed; call sites are separately checked.
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import yaml
from dotenv import dotenv_values, set_key

from .alibaba_inputs import endpoint
from .hermes_compat import patch_telegram
from .network import object_map


def install_files(profile: Path, env_file: Path) -> None:
    """Install plugin assets and update only an unmodified template persona.

    Args:
        profile (Path): Existing named Hermes profile directory.
        env_file (Path): Project dotenv file containing optional DRAWER settings.

    Returns:
        None: Files and Telegram's toolset selection are installed on disk.

    Raises:
        OSError: Reading, copying or writing a file fails.
        ValueError: A configured URL, data path or YAML mapping is invalid.
        KeyError: The configured timezone is unknown.
        UnicodeError: Configuration text is not UTF-8.
        yaml.YAMLError: The existing Hermes configuration is malformed.
        shutil.Error: Copying the plugin fails.
        RuntimeError: Checking or applying the Hermes album fix fails or times out.
    """  # noqa: DOC503 - File, config and timezone failures propagate.
    settings = dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")
    source = Path(
        settings.get("HERMES_SOURCE_DIR") or profile.parent.parent / "hermes-agent"
    )
    if (source / "plugins/platforms/telegram/adapter.py").exists():
        patch_telegram(source)
    timezone = settings.get("DRAWER_TIMEZONE") or "Europe/Kaliningrad"
    ZoneInfo(timezone)
    directory = Path(
        os.path.expandvars(
            settings.get("DRAWER_DATA_DIR") or str(profile / "drawer-data")
        )
    ).expanduser()
    base = settings.get("COMFYUI_BASE_URL") or "http://127.0.0.1:8188"
    anima_base = settings.get("COMFYUI_ANIMA_BASE_URL") or base
    if not directory.is_absolute() or any(
        urlsplit(url).scheme not in {"http", "https"} for url in (base, anima_base)
    ):
        message = "Use absolute DRAWER_DATA_DIR and HTTP(S) COMFYUI_*BASE_URL values."
        raise ValueError(message)
    if settings.get("DASHSCOPE_BASE_URL"):
        endpoint(str(settings["DASHSCOPE_BASE_URL"]))
    for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"):
        if key in settings:
            set_key(profile / ".env", key, settings[key] or "")
    source = Path(__file__).parent
    destination = profile / "plugins/drawer"
    persona = profile / "SOUL.md"
    previous_template = destination / "SOUL.md"
    update_persona = (
        not persona.exists()
        or not previous_template.exists()
        or persona.read_bytes() == previous_template.read_bytes()
    )
    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "settings.json"),
    )
    (destination / "settings.json").write_text(
        json.dumps(
            {
                "database": str(directory / "drawer.sqlite3"),
                "timezone": timezone,
                "comfy_url": base,
                "comfy_anima_url": anima_base,
            }
        ),
        encoding="utf-8",
    )
    if persona.exists() and not (profile / "SOUL.before-drawer.md").exists():
        shutil.copy2(persona, profile / "SOUL.before-drawer.md")
    if update_persona:
        shutil.copy2(source / "SOUL.md", persona)
    scripts = profile / "scripts"
    scripts.mkdir(exist_ok=True)
    (scripts / "drawer_tick.py").write_text(
        '"""Run the installed artist reminder worker without an LLM."""\n'
        "from pathlib import Path\n"
        "from drawer_assistant.reminders import tick\n"
        "tick(Path(__file__).resolve().parent.parent)\n",
        encoding="utf-8",
    )
    configure_toolset(profile)


def configure_toolset(profile: Path) -> None:
    """Select the artist-facing tools without exposing host administration to chat.

    Args:
        profile (Path): Existing Hermes profile directory.

    Returns:
        None: Artist/file/skill tools are selected, profile cwd set and notices hidden.

    Raises:
        OSError: Configuration cannot be read or replaced.
        UnicodeError: Configuration is not UTF-8.
        ValueError: Configuration mappings are malformed.
        yaml.YAMLError: The configuration YAML is malformed.
    """  # noqa: DOC502 - Includes propagated config errors.
    config_file = profile / "config.yaml"
    config = object_map(yaml.safe_load(config_file.read_text("utf-8-sig")))
    platforms = object_map(config.setdefault("platform_toolsets", {}))
    platforms["telegram"] = [
        "drawer",
        "image_gen",
        "vision",
        "web",
        "clarify",
        "memory",
        "file",
        "skills",
    ]
    terminal = object_map(config.setdefault("terminal", {}))
    terminal["cwd"] = str(profile.resolve())
    display = object_map(config.setdefault("display", {}))
    platform_display = object_map(display.setdefault("platforms", {}))
    telegram_display = object_map(platform_display.setdefault("telegram", {}))
    telegram_display["memory_notifications"] = "off"
    telegram_config = object_map(config.setdefault("telegram", {}))
    object_map(telegram_config.setdefault("extra", {}))["drawer_complete_albums"] = True
    temporary = config_file.with_suffix(".drawer.tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    temporary.replace(config_file)


def cron_identifier(profile: Path) -> str:
    """Find the existing reminder job without modifying Hermes' own cron store.

    Args:
        profile (Path): Named Hermes profile directory.

    Returns:
        str: Existing job identifier, or an empty string when absent.

    Raises:
        OSError: An existing cron file cannot be read.
        UnicodeError: Cron metadata is not UTF-8.
        ValueError: Cron JSON, object structure or job multiplicity is invalid.
        TypeError: Cron jobs are not stored as a list.
        KeyError: A matching job lacks its identifier.
    """  # noqa: DOC503 - JSON and filesystem failures propagate.
    filename = profile / "cron/jobs.json"
    if not filename.exists():
        return ""
    jobs = object_map(json.loads(filename.read_text("utf-8-sig"))).get("jobs", [])
    if not isinstance(jobs, list):
        message = "Hermes cron store must contain a jobs list."
        raise TypeError(message)
    # Validate the JSON list before narrowing each element with object_map.
    records = [object_map(item) for item in cast("list[object]", jobs)]
    matching = [item for item in records if item.get("name") == "drawer-reminders"]
    if len(matching) > 1:
        message = "Multiple drawer-reminders jobs exist; keep exactly one."
        raise ValueError(message)
    return str(matching[0]["id"]) if matching else ""


def install(profile: Path, env_file: Path) -> None:
    """Enable the installed plugin and create or update one native minute cron job.

    Args:
        profile (Path): Standard named Hermes profile directory.
        env_file (Path): Project dotenv configuration filename.

    Returns:
        None: Plugin admission and reminder scheduling have succeeded.

    Raises:
        RuntimeError: Hermes is missing or rejects/times out during installation.
        OSError: Local configuration or process startup fails.
        ValueError: Configuration or cron metadata is invalid.
        TypeError: Configured toolsets or cron jobs are not lists.
        KeyError: Required cron data or timezone is missing.
        UnicodeError: Local configuration cannot be decoded.
        yaml.YAMLError: Hermes configuration is malformed.
        shutil.Error: Plugin files cannot be copied.
    """  # noqa: DOC503 - Delegated installation and parsing errors propagate.
    launcher = shutil.which("hermes.cmd" if os.name == "nt" else "hermes")
    if launcher is None:
        message = (
            "Hermes is missing from PATH; open a new terminal after installing it."
        )
        raise RuntimeError(message)
    install_files(profile, env_file)
    identifier = cron_identifier(profile)
    schedule = (
        ["edit", identifier, "--schedule", "every 1m"]
        if identifier
        else ["create", "every 1m"]
    )
    commands = [
        ["plugins", "enable", "drawer"],
        [
            "cron",
            *schedule,
            "--name",
            "drawer-reminders",
            "--no-agent",
            "--script",
            "drawer_tick.py",
            "--interpreter",
            sys.executable,
            "--deliver",
            "local",
            "--failure-deliver",
            "local",
        ],
    ]
    for arguments in commands:
        try:
            # Fixed native subcommands, no shell or user-controlled command text.
            subprocess.run(  # noqa: S603 # nosec B603
                [launcher, "--profile", profile.name, *arguments],
                check=True,
                capture_output=True,
                timeout=180,
            )
        except subprocess.SubprocessError, OSError:
            message = (
                f"Hermes {arguments[0]} setup failed; run that command to inspect it."
            )
            raise RuntimeError(message) from None
