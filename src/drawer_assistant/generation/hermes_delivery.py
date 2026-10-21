# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Bridge trusted Hermes hook context and its native durable delivery queue."""

import sqlite3
from importlib import import_module
from pathlib import Path  # noqa: TC003 - Hermes evaluates hook annotations at runtime.
from typing import TYPE_CHECKING, Protocol, cast

from drawer_assistant.network import object_map

from .delivery import START_TOOLS, capture

if TYPE_CHECKING:
    from collections.abc import Callable


class Queue(Protocol):  # pylint: disable=too-few-public-methods
    """Describe only the native Hermes queue functions consumed by the worker."""

    def enqueue(
        self, execution_id: str, job: dict[str, object], content: str
    ) -> dict[str, object]:
        """Persist one idempotent native delivery request.

        Args:
            execution_id (str): Stable delivery id scoped to this profile.
            job (dict[str, object]): Origin and native delivery options.
            content (str): Short message with local MEDIA attachments.

        Returns:
            dict[str, object]: Native queue receipt and durable disposition.

        Raises:
            OSError: Native queue cannot be accessed.
            RuntimeError: Native Hermes rejects the operation.
            ValueError: Queue data is invalid.
            sqlite3.Error: Queue storage fails.
        """  # noqa: DOC502 - Protocol documents external implementation.
        del execution_id, job, content
        return {}


def queue() -> Queue:
    """Load the host-owned queue without requiring Hermes in standalone MCP.

    Returns:
        Queue: Native enqueue API with restart-safe at-most-once claim semantics.

    Raises:
        ImportError: This worker is running outside the supported Hermes runtime.
    """  # noqa: DOC502 - Dynamic import can fail.
    # Hermes is supplied by the host image, not a project/PyPI dependency.
    return cast("Queue", import_module("cron.delivery_queue"))


def before_tool(settings_file: Path, **context: object) -> dict[str, str] | None:
    """Capture routing before GPU work using the native plugin hook.

    Args:
        settings_file (Path): Owning profile's plugin configuration.
        **context (object): Host tool_name, args and unused observability fields.

    Returns:
        dict[str, str] | None: A blocking directive if routing cannot be persisted;
            otherwise None so native tool execution proceeds unchanged.
    """
    tool = str(context.get("tool_name", ""))
    if tool not in START_TOOLS:
        return None
    try:
        # Hermes exposes request-local values through ContextVars, not MCP env.
        getter = cast(
            "Callable[[str, str], str]",
            import_module("gateway.session_context").get_session_env,
        )
        origin = {
            key: getter("HERMES_SESSION_" + key.upper(), "")
            for key in ("platform", "chat_id", "thread_id", "user_id")
        }
        capture(settings_file, tool, object_map(context.get("args", {})), origin)
    except (
        OSError,
        ValueError,
        RuntimeError,
        ImportError,
        KeyError,
        TypeError,
        sqlite3.Error,
    ):
        return {
            "action": "block",
            "message": "Could not persist delivery routing; nothing submitted.",
        }
    return None
