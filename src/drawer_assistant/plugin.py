# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Expose the artist's persistent tools through Hermes' native plugin API."""

import json
import sqlite3
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from . import comfy
from .network import object_map
from .store import orders, references

if TYPE_CHECKING:
    from collections.abc import Callable


def read_persona(settings_file: Path, params: dict[str, str]) -> dict[str, str]:
    """Read only the installed profile's persona, independently of chat memory.

    Args:
        settings_file (Path): Installed plugin settings in profile/plugins/drawer.
        params (dict[str, str]): Must be empty; arbitrary paths are never accepted.

    Returns:
        dict[str, str]: Profile name, fixed source filename and current persona text.

    Raises:
        ValueError: Any tool argument is supplied.
        OSError: The profile persona cannot be read.
        UnicodeError: The persona is not UTF-8.
    """  # noqa: DOC503 - File reading and decoding failures propagate.
    if params:
        message = "Persona reading takes no arguments."
        raise ValueError(message)
    profile = settings_file.parent.parent.parent
    return {
        "profile": profile.name,
        "source": "SOUL.md",
        "text": (profile / "SOUL.md").read_text("utf-8"),
    }


# A structural protocol intentionally specifies only the single consumed host API.
class PluginContext(Protocol):  # pylint: disable=too-few-public-methods
    """Describe only the registration method consumed from the external host."""

    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict[str, object],
        handler: Callable[..., str],
    ) -> object:
        """Register a model-facing tool in the current Hermes profile.

        Args:
            name (str): Unique tool name.
            toolset (str): Native toolset containing the tool.
            schema (dict[str, object]): Function name, description and JSON schema.
            handler (Callable[..., str]): Handler accepting parameters and context.

        Returns:
            object: Opaque Hermes registration handle, not used by this plugin.

        Raises:
            RuntimeError: The host rejects registration.
        """  # noqa: DOC502 - Exceptions belong to the external host implementation.
        del name, toolset, schema, handler
        return NotImplemented


def handle(
    params: dict[str, object], *, tool: str, settings_file: Path, **context: object
) -> str:
    """Dispatch a native tool call while containing errors at the host boundary.

    Args:
        params (dict[str, object]): Model-supplied JSON arguments, all string values.
        tool (str): Registered tool name selecting the operation.
        settings_file (Path): This installed plugin's profile-local settings file.
        **context (object): Hermes execution metadata; unused by these local tools.

    Returns:
        str: JSON result or a sanitized error; no tokens or server response bodies.
    """
    del context
    try:
        if any(not isinstance(value, str) for value in params.values()):
            return json.dumps({"error": "All arguments must be strings."})
        arguments = {key: str(value) for key, value in params.items()}
        if tool == "drawer_persona":
            return json.dumps(
                {"result": read_persona(settings_file, arguments)}, ensure_ascii=False
            )
        settings = object_map(json.loads(settings_file.read_text("utf-8")))
        database = Path(str(settings["database"]))
        base = str(settings["comfy_url"]).rstrip("/")
        result: object
        if tool == "drawer_orders":
            if arguments.get("action") == "create":
                arguments.setdefault("timezone", str(settings["timezone"]))
            result = orders(database, arguments)
        elif tool == "drawer_references":
            result = references(database, arguments)
        elif arguments.get("action") == "start":
            result = comfy.start(database, base, arguments)
        elif arguments.get("action") == "status":
            result = comfy.status(database, base, arguments["id"])
        else:
            return json.dumps({"error": "Unknown action; use start or status."})
        return json.dumps({"result": result}, ensure_ascii=False)
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        RuntimeError,
        sqlite3.Error,
        OverflowError,
    ) as exc:
        return json.dumps(
            {
                "error": type(exc).__name__,
                "hint": "Check arguments, setup and service; no success confirmed.",
            }
        )


def register(ctx: PluginContext) -> None:
    """Register tools from the packaged schemas with profile-local dispatchers.

    Args:
        ctx (PluginContext): Registration facade supplied by Hermes.

    Returns:
        None: Artist tools and read-only persona inspection are registered.

    Raises:
        OSError: The packaged schema file cannot be read.
        UnicodeError: The schema file is not UTF-8.
        ValueError: Schema JSON or a schema mapping is malformed.
        RuntimeError: Hermes rejects a tool registration.
    """  # noqa: DOC502 - Packaged data and external host failures propagate.
    directory = Path(__file__).parent
    schemas = object_map(json.loads((directory / "tools.json").read_text("utf-8")))
    for tool, value in schemas.items():
        ctx.register_tool(
            name=tool,
            toolset="drawer",
            schema=object_map(value),
            handler=partial(
                handle, tool=tool, settings_file=directory / "settings.json"
            ),
        )
