# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Expose the artist's persistent tools through Hermes' native plugin API."""

import json
import sqlite3
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from . import comfy
from .anima_batch import batch, multipanel
from .database import configured_database
from .generation.hermes_delivery import before_tool
from .image_sheet import sheet
from .network import object_map
from .order_views import order_digest, order_info
from .references import references
from .stages import stages
from .store import orders
from .time_display import TIMEZONE

if TYPE_CHECKING:
    from collections.abc import Callable

    from .database import Location


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

    def register_hook(self, hook_name: str, callback: Callable[..., object]) -> object:
        """Register a host lifecycle observer in the owning profile.

        Args:
            hook_name (str): Native Hermes hook identifier.
            callback (Callable[..., object]): Callback receiving trusted context.

        Returns:
            object: Unused opaque registration handle.

        Raises:
            RuntimeError: The host rejects registration.
        """  # noqa: DOC502 - Host implementation can reject registration.
        del hook_name, callback
        return NotImplemented


def gpu_tool(
    path: Location, base: str, tool: str, arguments: dict[str, str]
) -> dict[str, str]:
    """Bind each ComfyUI model tool to its own fixed workflow for start and status.

    Args:
        path (Location): Artist database.
        base (str): Configured ComfyUI URL.
        tool (str): Registered model-specific tool or legacy drawer_generate.
        arguments (dict[str, str]): Action, id and optional generation prompt.

    Returns:
        dict[str, str]: ComfyUI job metadata and available image.

    Raises:
        ValueError: Action, arguments, API response or database schema is invalid.
        KeyError: Tool, required argument or API field is unknown.
        OSError: Database, workflow, random source or result cannot be accessed.
        UnicodeError: Workflow or API response cannot be decoded.
        TypeError: An API graph cannot be serialized.
        RuntimeError: ComfyUI fails or GPU-only mode is not enabled.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: Database operations fail.
    """  # noqa: DOC503 - Delegated generation and storage errors propagate.
    workflows = {
        "drawer_zimage": comfy.WORKFLOWS[0],
        "drawer_flux_klein": comfy.WORKFLOWS[1],
        "drawer_anima": comfy.WORKFLOWS[2],
        "drawer_generate": arguments.get("workflow", comfy.WORKFLOWS[0]),
    }
    workflow_name = workflows[tool]
    if arguments.get("action") == "start":
        return comfy.start(path, base, {**arguments, "workflow": workflow_name})
    if arguments.get("action") == "status":
        if tool == "drawer_anima":
            return comfy.wait_status(path, base, arguments["id"], workflow_name)
        return comfy.status(
            path,
            base,
            arguments["id"],
            workflow_name="" if tool == "drawer_generate" else workflow_name,
        )
    message = "Use start or status."
    raise ValueError(message)


def order_tool(
    path: Location, tool: str, arguments: dict[str, str], timezone: str = TIMEZONE
) -> object:
    """Dispatch orders, stages, references and read-only views.

    Args:
        path (Location): Artist database filename.
        tool (str): Registered order/reference/stage/view tool name.
        arguments (dict[str, str]): Validated string arguments for the selected tool.
        timezone (str, default=TIMEZONE): Configured default zone for new orders.

    Returns:
        object: Nested stages, an order card or the seven-day review snapshot.

    Raises:
        ValueError: Stage fields, schema, dates or tree are invalid.
        KeyError: A required field, order, parent, image or stage is missing.
        OSError: Database files cannot be accessed.
        RuntimeError: PostgreSQL operations fail.
        sqlite3.Error: Reading or committing data fails.
        OverflowError: Date conversion exceeds datetime bounds.
        RuntimeError: Downloading an explicitly selected reference fails.
    """  # noqa: DOC502 - The selected implementation raises indirectly.
    if tool == "drawer_orders":
        if arguments.get("action") == "create":
            arguments.setdefault("timezone", timezone)
        return orders(path, arguments)
    if tool == "drawer_references":
        return references(path, arguments)
    if tool == "drawer_stages":
        return stages(path, arguments)
    if tool == "drawer_order_info":
        return order_info(path, arguments["id"])
    return order_digest(path)


def handle(
    params: dict[str, object], *, tool: str, settings_file: Path, **context: object
) -> str:
    """Dispatch a native tool call while containing errors at the host boundary.

    Args:
        params (dict[str, object]): JSON arguments; sheets/batches accept lists
            and integers, other tools accept string values only.
        tool (str): Registered tool name selecting the operation.
        settings_file (Path): This installed plugin's profile-local settings file.
        **context (object): Hermes execution metadata; unused by these local tools.

    Returns:
        str: JSON result or a sanitized error; no tokens or server response bodies.
    """
    del context
    try:
        result: object
        if tool in {"drawer_image_sheet", "drawer_anima_batch"}:
            settings = object_map(json.loads(settings_file.read_text("utf-8")))
            result = (
                batch(
                    configured_database(settings_file, settings),
                    str(
                        settings.get("comfy_anima_url") or settings["comfy_url"]
                    ).rstrip("/"),
                    params,
                    str(settings.get("imagemagick_binary") or ""),
                )
                if tool == "drawer_anima_batch"
                else sheet(
                    configured_database(settings_file, settings).parent
                    / "image-sheets",
                    params,
                    str(settings.get("imagemagick_binary") or ""),
                )
            )
            return json.dumps({"result": result}, ensure_ascii=False)
        error: dict[str, str] = {}
        if any(not isinstance(value, str) for value in params.values()):
            error = {"error": "All arguments must be strings."}
        arguments = {key: str(value) for key, value in params.items()}
        if (
            not error
            and tool == "drawer_anima"
            and arguments.get("action") == "start"
            and multipanel(arguments.get("prompt", ""))
        ):
            error = {
                "error": "UseAnimaBatch",
                "hint": (
                    "Use drawer_anima_batch with one separate prompt per image "
                    "and columns for the grid. Do not submit a grid as one prompt."
                ),
            }
        if error:
            return json.dumps(error)
        if tool == "drawer_persona":
            return json.dumps(
                {"result": read_persona(settings_file, arguments)}, ensure_ascii=False
            )
        settings = object_map(json.loads(settings_file.read_text("utf-8")))
        database = configured_database(settings_file, settings)
        base = str(settings["comfy_url"]).rstrip("/")
        if tool == "drawer_anima":
            base = str(settings.get("comfy_anima_url") or base).rstrip("/")
        if tool in {
            "drawer_orders",
            "drawer_references",
            "drawer_stages",
            "drawer_order_info",
            "drawer_order_digest",
        }:
            result = order_tool(
                database, tool, arguments, str(settings.get("timezone") or TIMEZONE)
            )
        elif tool == "drawer_anima":
            result = gpu_tool(database, base, tool, arguments)
        else:
            return json.dumps(
                {
                    "error": "UnsupportedTool",
                    "hint": "Only Anima generation is enabled.",
                }
            )
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
    settings_file = directory / "settings.json"
    mcp = (
        settings_file.exists()
        and object_map(json.loads(settings_file.read_text("utf-8"))).get(
            "generation_mcp"
        )
        is True
    )
    if mcp:
        ctx.register_hook("pre_tool_call", partial(before_tool, settings_file))
    for tool, value in schemas.items():
        if mcp and tool in {"drawer_anima", "drawer_anima_batch", "drawer_image_sheet"}:
            continue
        ctx.register_tool(
            name=tool,
            toolset="drawer_read"
            if tool
            in {
                "drawer_order_info",
                "drawer_order_digest",
            }
            else "drawer",
            schema=object_map(value),
            handler=partial(
                handle, tool=tool, settings_file=directory / "settings.json"
            ),
        )
