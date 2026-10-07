# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Store reference metadata and deduplicated binary images in the same database."""

import hashlib
import re
from typing import TYPE_CHECKING

from .reference_files import load_image, materialize
from .store import MAX_FIELD, row_values, transaction

if TYPE_CHECKING:
    import sqlite3
    from pathlib import Path

FIELDS = ("title", "url", "order_id", "notes")
METADATA = """
SELECT refs.*, COALESCE(reference_files.sha256, '') AS sha256,
 COALESCE(reference_files.filename, '') AS filename,
 COALESCE(assets.mime, '') AS mime, COALESCE(length(assets.data), 0) AS size_bytes,
 CASE WHEN assets.sha256 IS NULL THEN 'link_only' ELSE 'blob' END AS storage
FROM refs LEFT JOIN reference_files ON refs.id=reference_files.reference_id
LEFT JOIN assets ON reference_files.sha256=assets.sha256
"""


def save_reference(
    database: sqlite3.Connection,
    params: dict[str, str],
    image: tuple[str, str, bytes] | None,
) -> None:
    """Upsert metadata and optional image within the caller's atomic transaction.

    Args:
        database (sqlite3.Connection): Open writable transaction using Row objects.
        params (dict[str, str]): Stable id and changed metadata fields. New references
            need title and either url or an image; omitted fields retain old values.
        image (tuple[str, str, bytes] | None): Filename, MIME and bounded image bytes,
            or None to retain the existing binary while editing metadata.

    Returns:
        None: Metadata and binary association are written; caller commits both.

    Raises:
        ValueError: Identifier, title, URL, length or source is invalid.
        KeyError: Required id or linked order is missing.
        sqlite3.Error: Reading or writing the database fails.
    """  # noqa: DOC503 - SQL and mapping errors propagate.
    identifier = params["id"]
    previous = database.execute(
        "SELECT * FROM refs WHERE id=?", (identifier,)
    ).fetchone()
    values = {field: str(previous[field]) if previous else "" for field in FIELDS}
    values.update({field: params[field] for field in FIELDS if field in params})
    if params.get("image_url") and "url" not in params:
        values["url"] = params["image_url"]
    has_image = (
        image is not None
        or database.execute(
            "SELECT 1 FROM reference_files WHERE reference_id=?", (identifier,)
        ).fetchone()
        is not None
    )
    invalid = (
        not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier),
        not values["title"].strip(),
        bool(values["url"]) and not re.match(r"https?://[^/\s]+", values["url"]),
        not values["url"] and not has_image,
        any(len(value) > MAX_FIELD for value in values.values()),
    )
    if any(invalid):
        message = "Use a stable id, title, image or HTTP(S) URL; fields <=4000 chars."
        raise ValueError(message)
    if (
        values["order_id"]
        and not database.execute(
            "SELECT 1 FROM orders WHERE id=?", (values["order_id"],)
        ).fetchone()
    ):
        raise KeyError(values["order_id"])
    database.execute(
        "INSERT INTO refs(id,title,url,order_id,notes) VALUES(?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET title=excluded.title,url=excluded.url,"
        "order_id=excluded.order_id,notes=excluded.notes",
        (identifier, *(values[field] for field in FIELDS)),
    )
    if image is not None:
        filename, mime, data = image
        digest = hashlib.sha256(data).hexdigest()
        database.execute(
            "INSERT INTO assets(sha256,mime,data) VALUES(?,?,?) "
            "ON CONFLICT(sha256) DO NOTHING",
            (digest, mime, data),
        )
        database.execute(
            "INSERT INTO reference_files(reference_id,sha256,filename) VALUES(?,?,?) "
            "ON CONFLICT(reference_id) DO UPDATE SET sha256=excluded.sha256,"
            "filename=excluded.filename",
            (identifier, digest, filename),
        )
        database.execute(
            "DELETE FROM assets WHERE NOT EXISTS "
            "(SELECT 1 FROM reference_files WHERE reference_files.sha256=assets.sha256)"
        )


def references(path: Path, params: dict[str, str]) -> list[dict[str, str]]:
    """Save binary references, list metadata or restore one image for delivery.

    Args:
        path (Path): SQLite filename; exports live in reference-exports beside it.
        params (dict[str, str]): Action save/list/get. save takes id, metadata and
            optional file/image_url; list optionally filters by order_id; get needs id.

    Returns:
        list[dict[str, str]]: Metadata without binary/Base64 data. save/get return one
            reference; get with storage=blob also returns file and MEDIA marker.

    Raises:
        ValueError: Action, fields, image, integrity or database schema is invalid.
        KeyError: Required argument, reference, linked order or MIME is missing.
        OSError: Image files, exports or database directory cannot be accessed.
        RuntimeError: Downloading an explicitly requested image fails.
        sqlite3.Error: Reading or committing the transaction fails.
    """  # noqa: DOC503 - Includes delegated storage and image errors.
    action = params["action"]
    if action not in {"list", "save", "get"}:
        message = "References action must be list, save or get."
        raise ValueError(message)
    image = load_image(params) if action == "save" else None
    with transaction(path) as database:
        database.execute("BEGIN IMMEDIATE" if action == "save" else "BEGIN")
        if action == "save":
            save_reference(database, params, image)
        if action == "list":
            return [
                row_values(row)
                for row in database.execute(
                    METADATA + " WHERE ?='' OR refs.order_id=? ORDER BY refs.id",
                    (params.get("order_id", ""), params.get("order_id", "")),
                )
            ]
        row = database.execute(
            METADATA + " WHERE refs.id=?", (params["id"],)
        ).fetchone()
        if row is None:
            raise KeyError(params["id"])
        result = row_values(row)
        if action == "get" and result["storage"] == "blob":
            data = bytes(
                database.execute(
                    "SELECT data FROM assets WHERE sha256=?", (result["sha256"],)
                ).fetchone()[0]
            )
            file = materialize(
                path.parent / "reference-exports",
                data,
                result["sha256"],
                result["mime"],
            )
            result.update(file=str(file), media=f"MEDIA:{file}")
        return [result]
