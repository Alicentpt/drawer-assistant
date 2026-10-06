# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Small typed HTTP boundary with bounded responses and sanitized failures."""

import json
from http.client import HTTPException
from typing import cast
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

MAX_RESPONSE = 32 * 1024 * 1024


def object_map(value: object) -> dict[str, object]:
    """Validate a JSON object before narrowing its untyped parser result.

    Args:
        value (object): Parsed JSON value.

    Returns:
        dict[str, object]: Validated mapping with string keys.

    Raises:
        ValueError: The value is not an object with string keys.
    """
    # JSON parsers expose Any; validate the container and keys at this boundary.
    entries = cast("dict[object, object]", value) if isinstance(value, dict) else {}
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in entries):
        message = "Expected a JSON object."
        raise ValueError(message)
    return cast("dict[str, object]", value)  # Validated immediately above.


def request(url: str, payload: dict[str, object] | None = None) -> bytes:
    """Make one HTTP request without retrying possibly successful mutations.

    Args:
        url (str): HTTP(S) URL supplied by configuration or a fixed API route.
        payload (dict[str, object] | None, default=None): JSON POST body; None uses GET.

    Returns:
        bytes: Response body, limited to 32 MiB.

    Raises:
        ValueError: URL is not HTTP(S), or a JSON payload cannot be serialized.
        TypeError: The payload contains a non-JSON value.
        RuntimeError: Request fails, times out or exceeds the response limit.
    """  # noqa: DOC503 - JSON serialization errors propagate.
    address = urlsplit(url)
    if address.scheme not in {"http", "https"}:
        message = "Only HTTP(S) API endpoints are supported."
        raise ValueError(message)
    body = None if payload is None else json.dumps(payload).encode()
    try:
        # URL schemes are restricted above; endpoints come from operator config.
        http_request = Request(  # noqa: S310
            url, data=body, headers={"Content-Type": "application/json"}
        )
        proxy = (
            ProxyHandler({})
            if address.hostname in {"127.0.0.1", "localhost", "::1"}
            else ProxyHandler()
        )
        with build_opener(proxy).open(http_request, timeout=30) as response:
            data: bytes = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            message_text = "API response exceeds 32 MiB."
            raise RuntimeError(message_text)
    except (OSError, URLError, HTTPException, ValueError) as exc:
        # Telegram URLs contain a bot token, so never expose the URL or exception.
        message_text = f"API request failed ({type(exc).__name__}); check connection."
        raise RuntimeError(message_text) from None
    return data


def api(url: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    """Read a JSON API object over the bounded HTTP transport.

    Args:
        url (str): Operator-configured HTTP(S) URL.
        payload (dict[str, object] | None, default=None): JSON body; None selects GET.

    Returns:
        dict[str, object]: Validated JSON object.

    Raises:
        ValueError: The URL, response JSON or object shape is invalid.
        TypeError: The payload contains a non-JSON value.
        UnicodeError: The response is not a supported JSON encoding.
        RuntimeError: The request fails or the response is too large.
    """  # noqa: DOC502 - Transport and parser failures propagate.
    return object_map(json.loads(request(url, payload)))
