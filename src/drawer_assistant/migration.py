# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Snapshot SQLite and atomically copy validated records and assets to PostgreSQL."""

import argparse
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values

from .postgres import SCHEMA_VERSION, Postgres, connect_postgres
from .store import connect_sqlite

if TYPE_CHECKING:
    from .database import Connection, Row

TABLES = (
    "orders",
    "refs",
    "jobs",
    "alibaba_jobs",
    "assets",
    "reference_files",
    "order_stages",
    "stage_images",
    "image_batches",
    "generation_tasks",
)
type Manifest = dict[str, dict[str, str | int]]


def readonly(path: Path) -> sqlite3.Connection:
    """Open an existing source without permitting writes or creating a new file.

    Args:
        path (Path): Existing SQLite database or previously created snapshot.

    Returns:
        sqlite3.Connection: Read-only connection with named rows; caller closes it.

    Raises:
        OSError: Resolving the source path fails.
        sqlite3.Error: The source cannot be opened.
    """  # noqa: DOC502 - Filesystem and driver errors propagate.
    database = sqlite3.connect(
        path.resolve().as_uri() + "?mode=ro", uri=True, timeout=35
    )
    database.row_factory = sqlite3.Row
    return database


def snapshot(source: Path, destination: Path) -> None:
    """Create a consistent SQLite backup and upgrade only that new copy.

    Args:
        source (Path): Existing live database; never modified by this operation.
        destination (Path): New backup filename; existing files are never replaced.

    Returns:
        None: A complete, current-schema snapshot is available for verification.

    Raises:
        OSError: A path cannot be accessed or the destination already exists.
        sqlite3.Error: Backup or migration of the copy fails.
        ValueError: The source uses a newer unsupported schema.
    """  # noqa: DOC502 - Backup and migration failures propagate.
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb"):
        pass
    with (
        closing(readonly(source)) as original,
        closing(sqlite3.connect(destination)) as backup,
    ):
        original.backup(backup)
    with closing(connect_sqlite(destination)):
        pass


def row_digest(row: Row) -> bytes:
    """Hash every field with length boundaries, preserving raw binary image bytes.

    Args:
        row (Row): Named SQL row with bytes, text, numeric or null field values.

    Returns:
        bytes: SHA-256 digest independent of database row ordering or collation.

    Raises:
        TypeError: A field contains an unsupported non-SQL JSON value.
        ValueError: A numeric value is not finite.
        UnicodeError: Text fields cannot be encoded as UTF-8.
    """  # noqa: DOC502 - JSON encoding failures propagate.
    digest = hashlib.sha256()
    for key in sorted(row.keys()):
        value: object = row[key]
        data = (
            b"B" + value
            if isinstance(value, bytes)
            else b"J" + json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
        )
        name = key.encode()
        digest.update(len(name).to_bytes(8) + name + len(data).to_bytes(8) + data)
    return digest.digest()


def inventory(database: Connection) -> Manifest:
    """Count and hash every application table while validating stored asset hashes.

    Args:
        database (Connection): Open source or destination transaction.

    Returns:
        Manifest: Counts and content fingerprints, without private record text.

    Raises:
        sqlite3.Error: Reading a SQLite table fails.
        RuntimeError: Reading a PostgreSQL table fails.
        ValueError: Image bytes disagree with SHA-256 or a numeric value is invalid.
        TypeError: Stored fields have unsupported types.
        UnicodeError: A stored text field cannot be encoded.
    """  # noqa: DOC503 - Row hashing can also fail.
    result: Manifest = {}
    for table in TABLES:
        hashes: list[bytes] = []
        # Table names come only from TABLES, never from CLI or model arguments.
        for row in database.execute(f'SELECT * FROM "{table}"'):  # noqa: S608 # nosec B608
            if (
                table == "assets"
                and row["sha256"] != hashlib.sha256(bytes(row["data"])).hexdigest()
            ):
                message = "An asset failed SHA-256 verification; migration aborted."
                raise ValueError(message)
            hashes.append(row_digest(row))
        result[table] = {
            "rows": len(hashes),
            "sha256": hashlib.sha256(b"".join(sorted(hashes))).hexdigest(),
        }
    return result


def copy_rows(source: sqlite3.Connection, destination: Postgres) -> None:
    """Insert all known tables in dependency order with deferred nested-stage links.

    Args:
        source (sqlite3.Connection): Consistent, validated read-only snapshot.
        destination (Postgres): Empty destination inside the caller's transaction.

    Returns:
        None: Rows are inserted without committing or changing any identifiers.

    Raises:
        sqlite3.Error: Source tables cannot be read.
        RuntimeError: Destination writes or deferred constraints fail.
    """  # noqa: DOC502 - Driver failures propagate.
    for table in TABLES:
        # Fixed table list; source column names are individually quoted as identifiers.
        cursor = source.execute(f'SELECT * FROM "{table}"')  # noqa: S608 # nosec B608
        columns = ",".join(
            '"' + column[0].replace('"', '""') + '"'
            for column in cursor.description or ()
        )
        placeholders = ",".join("?" for _ in cursor.description or ())
        # Identifiers above are fixed/quoted; values below stay bound.
        statement = f'INSERT INTO "{table}"({columns}) VALUES({placeholders})'  # nosec
        for row in cursor:
            destination.execute(statement, tuple(row))


def transfer(source: Path, url: str, *, apply: bool = False) -> Manifest:
    """Verify or atomically copy a snapshot, refusing to overwrite differing data.

    Args:
        source (Path): Current-schema SQLite snapshot; opened read-only.
        url (str): PostgreSQL URL, kept out of results and error messages.
        apply (bool, default=False): Copy only into an empty database. False only
            verifies existing tables; it does not create schema or insert rows.

    Returns:
        Manifest: Matching source/destination counts and full content fingerprints.

    Raises:
        OSError: Source path resolution fails.
        sqlite3.Error: Reading the source or integrity checks fails.
        RuntimeError: PostgreSQL connection, writes or verification fails.
        ValueError: Schema, foreign keys, asset hashes or destination data differ.
        TypeError: Stored fields cannot be encoded for verification.
        UnicodeError: Stored text cannot be encoded.
    """  # noqa: DOC503 - Includes delegated storage and integrity failures.
    with closing(readonly(source)) as original:
        original.execute("BEGIN")
        version = original.execute("PRAGMA user_version").fetchone()[0]
        if (
            version != SCHEMA_VERSION
            or original.execute("PRAGMA foreign_key_check").fetchone()
        ):
            message = "Use a current-schema snapshot with valid foreign keys."
            raise ValueError(message)
        expected = inventory(original)
        target = connect_postgres(url) if apply else Postgres(url)
        with closing(target), target:
            target.execute("BEGIN IMMEDIATE")
            existing = inventory(target)
            if existing == expected:
                return expected
            if not apply or any(state["rows"] for state in existing.values()):
                message = "Destination differs; no existing records were overwritten."
                raise ValueError(message)
            copy_rows(original, target)
            if inventory(target) != expected:
                message = "Content verification failed; all copied rows rolled back."
                raise ValueError(message)
    return expected


def main() -> None:
    """Run explicit snapshot, copy or read-only verification commands.

    Returns:
        None: A private-data-free inventory is printed after successful completion.

    Raises:
        SystemExit: Arguments are invalid or a migration error is safely reported.
    """  # noqa: DOC502 - argparse raises SystemExit indirectly.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("snapshot", "copy", "verify"))
    parser.add_argument("source", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        if args.action == "snapshot":
            if args.backup is None:
                parser.error("snapshot requires --backup pointing to a new file")
            snapshot(args.source, args.backup)
            with closing(readonly(args.backup)) as database:
                result = inventory(database)
        else:
            url = dotenv_values(
                args.env_file, interpolate=False, encoding="utf-8-sig"
            ).get("DRAWER_DATABASE_URL")
            if not url:
                parser.error("Set DRAWER_DATABASE_URL in the selected --env-file")
            result = transfer(args.source, url, apply=args.action == "copy")
    except (
        OSError,
        sqlite3.Error,
        ValueError,
        RuntimeError,
        TypeError,
        UnicodeError,
    ) as exc:
        # Driver messages and paths may contain secrets; do not print exception text.
        parser.exit(
            1, f"Migration failed ({type(exc).__name__}); no cutover confirmed.\n"
        )
    print(json.dumps(result, indent=2))  # noqa: T201 - Explicit CLI inventory output.
