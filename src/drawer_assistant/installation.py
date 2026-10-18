# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Install the persona, skills, native tools and reminder tick into Hermes."""

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
from .image_sheet import magick_path
from .network import object_map
from .time_display import TIMEZONE


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
    timezone = settings.get("DRAWER_TIMEZONE") or TIMEZONE
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
    set_key(profile / ".env", "HERMES_TIMEZONE", timezone)
    for key in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "DRAWER_DATABASE_URL"):
        if key in settings:
            set_key(profile / ".env", key, settings[key] or "")
    source = Path(__file__).parent
    destination = profile / "plugins/drawer"
    install_context(profile, source)
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
                "imagemagick_binary": magick_path(
                    settings.get("IMAGEMAGICK_BINARY") or ""
                ),
                "generation_mcp": True,
                "generation_auto_delivery": delivery_enabled(
                    destination / "settings.json"
                ),
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
    configure_toolset(profile, timezone)
    configure_generation_mcp(profile)


def delivery_enabled(settings_file: Path) -> bool:
    """Preserve an operator-enabled worker flag across normal plugin updates.

    Args:
        settings_file (Path): Existing installed plugin configuration, if any.

    Returns:
        bool: Previously enabled flag; new profiles default to no background sends.

    Raises:
        OSError: Existing settings cannot be read.
        ValueError: Existing settings are not valid JSON objects.
    """  # noqa: DOC502 - File and parser failures propagate.
    return (
        settings_file.exists()
        and object_map(json.loads(settings_file.read_text("utf-8"))).get(
            "generation_auto_delivery"
        )
        is True
    )


def configure_generation_mcp(profile: Path) -> None:
    """Connect the profile to its local stdio generation server.

    Args:
        profile (Path): Selected existing Hermes profile directory.

    Returns:
        None: The generation MCP is enabled for Telegram and ordinary CLI only;
            read-only cron tools stay unchanged. Other MCP servers are preserved.

    Raises:
        OSError: Profile YAML cannot be read or written.
        ValueError: Existing configuration mappings are malformed.
        yaml.YAMLError: YAML is invalid.
        TypeError: Telegram toolset selection is not a list.
    """  # noqa: DOC503 - File and configuration errors propagate.
    file = profile / "config.yaml"
    config = object_map(yaml.safe_load(file.read_text("utf-8")))
    object_map(config.setdefault("mcp_servers", {}))["drawer_generation"] = {
        "command": os.environ.get("DRAWER_GENERATION_PYTHON") or sys.executable,
        "args": [
            "-I",
            "-m",
            "drawer_assistant.mcp_generation",
            "--settings",
            str((profile / "plugins/drawer/settings.json").resolve()),
        ],
        "timeout": 120,
    }
    # Platform selection uses the MCP server name; runtime registry adds mcp-.
    platforms = object_map(config["platform_toolsets"])
    selected = platforms.get("telegram")
    if not isinstance(selected, list):
        message = "Telegram toolsets must be a list."
        raise TypeError(message)
    if "drawer_generation" not in selected:
        # The container shape is validated above; preserve other selected names.
        cast("list[object]", selected).append("drawer_generation")
    temporary = file.with_suffix(".mcp.tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True), "utf-8")
    temporary.replace(file)


def install_context(profile: Path, source: Path) -> None:
    """Install project rules, preserving edits made since the previous template.

    Args:
        profile (Path): Existing Hermes profile and configured working directory.
        source (Path): Package directory containing the new .hermes.md template.

    Returns:
        None: Missing or unmodified rules are installed before templates refresh.

    Raises:
        OSError: A rule file cannot be read or copied.
    """  # noqa: DOC502 - Filesystem errors propagate.
    current = profile / ".hermes.md"
    previous = profile / "plugins/drawer/.hermes.md"
    if not current.exists() or (
        previous.exists() and current.read_bytes() == previous.read_bytes()
    ):
        shutil.copy2(source / ".hermes.md", current)


def configure_toolset(profile: Path, timezone: str = TIMEZONE) -> None:
    """Select the artist-facing tools without exposing host administration to chat.

    Args:
        profile (Path): Existing Hermes profile directory.
        timezone (str, default=TIMEZONE): IANA zone for the native Hermes clock/cron.

    Returns:
        None: Tools, bundled skills and profile cwd are selected; notices are hidden.

    Raises:
        OSError: Configuration cannot be read or replaced.
        UnicodeError: Configuration is not UTF-8.
        ValueError: Configuration mappings or the external skill directory list
            are malformed.
        yaml.YAMLError: The configuration YAML is malformed.
    """  # noqa: DOC503 - YAML and filesystem exceptions propagate with ValueError.
    config_file = profile / "config.yaml"
    config = object_map(yaml.safe_load(config_file.read_text("utf-8-sig")))
    config["timezone"] = timezone
    skills = object_map(config.setdefault("skills", {}))
    directories = skills.setdefault("external_dirs", [])
    # YAML collections are untyped; validate elements after narrowing to objects.
    if not isinstance(directories, list) or not all(
        isinstance(item, str) for item in cast("list[object]", directories)
    ):
        message = "skills.external_dirs must be a list of paths."
        raise ValueError(message)
    bundled = str((profile / "plugins/drawer/skills").resolve())
    if bundled not in directories:
        cast("list[str]", directories).append(bundled)
    platforms = object_map(config.setdefault("platform_toolsets", {}))
    platforms["telegram"] = [
        "drawer",
        "drawer_read",
        "vision",
        "web",
        "clarify",
        "memory",
        "file",
        "skills",
    ]
    configure_cron_tools(config)
    object_map(config.setdefault("cron", {})).update(
        wrap_response=False, mirror_delivery=True
    )
    terminal = object_map(config.setdefault("terminal", {}))
    terminal["cwd"] = str(profile.resolve())
    display = object_map(config.setdefault("display", {}))
    platform_display = object_map(display.setdefault("platforms", {}))
    telegram_display = object_map(platform_display.setdefault("telegram", {}))
    telegram_display["memory_notifications"] = "off"
    telegram_display["tool_progress"] = "off"
    telegram_display["show_commentary"] = False
    telegram_extra = object_map(
        object_map(config.setdefault("telegram", {})).setdefault("extra", {})
    )
    telegram_extra["drawer_complete_albums"] = True
    telegram_extra["cron_continuable_surface"] = "in_channel"
    temporary = config_file.with_suffix(".drawer.tmp")
    temporary.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    temporary.replace(config_file)


def configure_cron_tools(config: dict[str, object]) -> None:
    """Select read-only cron tools using Hermes' saved plugin-toolset selection.

    Args:
        config (dict[str, object]): Mutable profile configuration mapping.

    Returns:
        None: Both plugin toolsets are known; only drawer_read is enabled for cron.

    Raises:
        ValueError: Toolset configuration mappings or the known list are malformed.
    """  # noqa: DOC503 - object_map validates native configuration mappings.
    object_map(config.setdefault("platform_toolsets", {}))["cron"] = [
        "drawer_read",
        "no_mcp",
    ]
    # Hermes auto-enables newly discovered plugin toolsets unless marked known.
    known = object_map(config.setdefault("known_plugin_toolsets", {}))
    cron_known = known.setdefault("cron", [])
    if not isinstance(cron_known, list) or not all(
        isinstance(item, str) for item in cast("list[object]", cron_known)
    ):
        message = "known_plugin_toolsets.cron must be a list of names."
        raise ValueError(message)
    for name in ("drawer", "drawer_read"):
        if name not in cron_known:
            # The checks above establish a mutable list containing only strings.
            cast("list[str]", cron_known).append(name)


def cron_identifier(profile: Path, name: str = "drawer-reminders") -> str:
    """Find the existing reminder job without modifying Hermes' own cron store.

    Args:
        profile (Path): Named Hermes profile directory.
        name (str, default="drawer-reminders"): Exact name of the managed cron job.

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
    matching = [item for item in records if item.get("name") == name]
    if len(matching) > 1:
        message = f"Multiple {name} jobs exist; keep exactly one."
        raise ValueError(message)
    return str(matching[0]["id"]) if matching else ""


def cron_command(
    profile: Path, name: str, schedule: str, prompt: str = ""
) -> list[str]:
    """Build an idempotent native cron create/edit command by exact job name.

    Args:
        profile (Path): Named Hermes profile containing cron metadata.
        name (str): Stable managed job name.
        schedule (str): Native Hermes interval or cron expression.
        prompt (str, default=""): Agent instruction; empty for a script-only job.

    Returns:
        list[str]: Fixed CLI arguments for creating or editing exactly one job.

    Raises:
        OSError: Cron metadata cannot be read.
        UnicodeError: Cron metadata is not UTF-8.
        ValueError: JSON or matching job multiplicity is invalid.
        TypeError: Jobs are not stored in a list.
        KeyError: A matching job lacks its identifier.
    """  # noqa: DOC502 - Metadata parsing errors propagate.
    identifier = cron_identifier(profile, name)
    action = (
        ["edit", identifier, "--schedule", schedule]
        if identifier
        else [
            "create",
            schedule,
        ]
    )
    if prompt:
        action.extend(["--prompt", prompt] if identifier else [prompt])
    return ["cron", *action, "--name", name]


def install(profile: Path, env_file: Path) -> None:
    """Enable the plugin, half-hour reminder worker and daily noon order review.

    Args:
        profile (Path): Standard named Hermes profile directory.
        env_file (Path): Project dotenv configuration filename.

    Returns:
        None: Plugin admission and both native cron schedules have succeeded.

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
    chat = dotenv_values(profile / ".env").get("TELEGRAM_ALLOWED_CHATS") or ""
    if not chat.startswith("-") or not chat[1:].isdigit():
        message = "Daily review requires one permitted Telegram group."
        raise ValueError(message)
    commands = [
        ["plugins", "enable", "drawer"],
        [
            *cron_command(profile, "drawer-reminders", "every 30m"),
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
        [
            *cron_command(
                profile,
                "drawer-order-review",
                "0 12 * * *",
                "Выполни ежедневную сводку заказов по навыку drawer-order-review. "
                "Сначала вызови drawer_order_digest; используй полученные данные.",
            ),
            "--skill",
            "drawer-order-review",
            "--workdir",
            str(profile.resolve()),
            "--deliver",
            f"telegram:{chat}",
            "--failure-deliver",
            f"telegram:{chat}",
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
