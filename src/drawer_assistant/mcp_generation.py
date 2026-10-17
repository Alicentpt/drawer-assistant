# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Expose fixed Nova generation capabilities through the official MCP stdio SDK."""

import argparse
import asyncio
import json
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from jsonschema import ValidationError
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .generation.presets import GenerationRequestError
from .generation.service import invoke, schemas
from .network import object_map

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from mcp.server.context import ServerRequestContext


@asynccontextmanager
async def lifespan(server: Server[None]) -> AsyncGenerator[None]:
    """Provide an explicitly typed empty SDK lifespan.

    Args:
        server (Server[None]): Owning server, unused.

    Yields:
        None: No connection or credential is held between tool calls.
    """
    del server
    yield None


def create_server(settings_file: Path) -> Server[None]:
    """Build MCP handlers; credentials and profile paths never enter tool schemas.

    Args:
        settings_file (Path): Selected profile's plugin settings file.

    Returns:
        Server[None]: MCP server with eight explicit, validated tools.
    """
    execution_lock = asyncio.Lock()

    async def list_tools(
        ctx: ServerRequestContext[None], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        """Advertise packaged schemas without any filesystem or network mutation.

        Args:
            ctx (ServerRequestContext[None]): SDK request context, unused.
            params (types.PaginatedRequestParams | None): Unused pagination options.

        Returns:
            types.ListToolsResult: All generation tools with strict input schemas.

        Raises:
            OSError: Packaged schemas cannot be read.
            ValueError: Packaged schemas are invalid.
            KeyError: A definition lacks required fields.
        """  # noqa: DOC502 - Schema loading and SDK validation errors propagate.
        del ctx, params
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=str(object_map(value)["description"]),
                    input_schema=object_map(object_map(value)["inputSchema"]),
                )
                for name, value in schemas().items()
            ]
        )

    async def call_tool(
        ctx: ServerRequestContext[None], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        """Execute blocking operations off-loop and contain private driver failures.

        Args:
            ctx (ServerRequestContext[None]): SDK request context, unused.
            params (types.CallToolRequestParams): Tool name and JSON arguments.

        Returns:
            types.CallToolResult: Structured data plus text/MEDIA, or sanitized error.
        """
        del ctx
        try:
            async with execution_lock:
                result = await asyncio.to_thread(
                    invoke,
                    settings_file,
                    params.name,
                    object_map(params.arguments or {}),
                )
        except ValidationError:
            return types.CallToolResult(
                is_error=True,
                content=[
                    types.TextContent(
                        type="text",
                        text="Invalid arguments; follow the published input schema.",
                    )
                ],
            )
        except GenerationRequestError as exc:
            # Only our fixed validation messages cross this boundary, not driver
            # exceptions, private URLs, paths or arbitrary third-party messages.
            return types.CallToolResult(
                is_error=True,
                content=[types.TextContent(type="text", text=str(exc))],
            )
        except (
            sqlite3.Error,
            OSError,
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
        ) as exc:
            return types.CallToolResult(
                is_error=True,
                content=[
                    types.TextContent(
                        type="text",
                        text=(
                            f"{type(exc).__name__}: operation not confirmed. "
                            "Check generation_health, task id and server. "
                            "Never resubmit an uncertain job."
                        ),
                    )
                ],
            )
        return types.CallToolResult(
            structured_content=result,
            content=[
                types.TextContent(
                    type="text", text=json.dumps(result, ensure_ascii=False)
                )
            ],
        )

    return Server(
        "drawer-generation",
        version="0.1.0",
        lifespan=lifespan,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def serve(settings_file: Path) -> None:
    """Serve MCP exclusively on stdin/stdout; diagnostics belong on stderr.

    Args:
        settings_file (Path): Profile-local settings supplied by operator CLI.

    Returns:
        None: Runs until the owning client closes stdio.

    Raises:
        OSError: The stdio transport cannot be opened.
        RuntimeError: SDK transport execution fails.
    """  # noqa: DOC502 - SDK transport failures propagate.
    server = create_server(settings_file)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main() -> None:
    """Start the stdio server with an explicit profile-local settings file.

    Returns:
        None: Runs until the MCP client disconnects.

    Raises:
        OSError: The transport cannot be opened.
        RuntimeError: SDK transport execution fails.
        SystemExit: CLI arguments are missing or invalid.
    """  # noqa: DOC502 - CLI and async transport failures propagate.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    asyncio.run(serve(parser.parse_args().settings))


if __name__ == "__main__":
    main()
