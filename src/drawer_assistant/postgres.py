# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Adapt the bounded repository SQL contract to ordinary PostgreSQL and AWS RDS."""

from contextlib import contextmanager
from typing import TYPE_CHECKING, Literal, Self, cast

import psycopg
from psycopg import conninfo, sql
from psycopg.rows import DictRow, dict_row

from .schema import (
    ALIBABA_SCHEMA,
    ASSETS_SCHEMA,
    BATCH_SCHEMA,
    REFERENCE_FILES_SCHEMA,
    SCHEMA,
    STAGE_SCHEMA,
)

if TYPE_CHECKING:
    from collections.abc import Generator, Sequence
    from types import TracebackType
    from typing import LiteralString

# One database-scoped transaction lock preserves SQLite's serialized writers.
WRITE_LOCK = 741938021
SCHEMA_VERSION = 6


@contextmanager
def storage_errors() -> Generator[None]:
    """Hide driver messages that can contain credentials, hosts or private SQL data.

    Yields:
        None: Driver operations run inside this sanitizing boundary.

    Raises:
        RuntimeError: PostgreSQL rejects an operation or becomes unreachable.
    """
    try:
        yield
    except psycopg.Error:
        message = (
            "PostgreSQL operation failed; inspect connectivity and database setup."
        )
        raise RuntimeError(message) from None


class Postgres:
    """Implement the repository's small connection contract with reusable contexts.

    Attributes:
        _connection (psycopg.Connection[DictRow]): Private driver connection.
    """  # noqa: DOC302 - Repo requires constructor Returns documentation.

    _connection: psycopg.Connection[DictRow]

    def __init__(self, url: str) -> None:
        """Connect with bounded waits, preserving URL TLS verification options.

        Args:
            url (str): PostgreSQL URL; RDS should use sslmode=verify-full and a CA.

        Returns:
            None: A connection is opened; caller initializes schema and closes it.

        Raises:
            ValueError: URL does not select PostgreSQL.
            RuntimeError: Authentication or the connection fails.
        """  # noqa: DOC303,DOC503 - Repo requires constructor Returns documentation.
        if not url.startswith(("postgresql://", "postgres://")):
            message = "DRAWER_DATABASE_URL must be a PostgreSQL URL."
            raise ValueError(message)
        with storage_errors():
            connection_options = conninfo.conninfo_to_dict(url).get("options", "")
            self._connection = psycopg.Connection[DictRow].connect(
                url,
                row_factory=dict_row,
                connect_timeout=15,
                options=f"{connection_options} "
                "-c statement_timeout=35000 -c lock_timeout=35000",
            )

    def __enter__(self) -> Self:
        """Start a reusable transaction context without closing on exit.

        Returns:
            Self: This connection, matching SQLite's context behavior.
        """
        return self

    def __exit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> Literal[False]:
        """Finish one transaction while keeping the connection reusable.

        Args:
            error_type (type[BaseException] | None): Exception type, or None.
            error (BaseException | None): Original exception, left unsuppressed.
            traceback (TracebackType | None): Original traceback, not inspected.

        Returns:
            Literal[False]: Never suppress an exception from the calling operation.

        Raises:
            RuntimeError: Committing or rolling back fails.
        """  # noqa: DOC502 - Transaction completion can fail.
        del error, traceback
        with storage_errors():
            if error_type is None:
                self._connection.commit()
            else:
                self._connection.rollback()
        return False

    def execute(
        self, statement: str, parameters: Sequence[object] = ()
    ) -> psycopg.Cursor[DictRow]:
        """Execute trusted repository SQL with bound values, never interpolated data.

        The SQL subset uses qmark placeholders and contains no literal question
        marks. BEGIN IMMEDIATE takes the shared transaction advisory lock; ordinary
        BEGIN relies on psycopg's implicit transaction. This is not a SQL parser.

        Args:
            statement (str): Trusted SQL from repository constants and fixed fields.
            parameters (Sequence[object], default=()): Separately adapted SQL values.

        Returns:
            psycopg.Cursor[DictRow]: Cursor providing schema-dependent named fields.

        Raises:
            RuntimeError: Executing SQL or waiting for the writer lock fails.
        """  # noqa: DOC502 - Driver failures are sanitized.
        with storage_errors():
            if statement == "BEGIN IMMEDIATE":
                return self._connection.execute(
                    "SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,)
                )
            if statement == "BEGIN":
                return self._connection.execute("SELECT 1")
            # Only trusted repository SQL crosses here. Placeholder adaptation
            # loses LiteralString typing; all user values remain separately bound.
            # Mypy erases LiteralString to str; Pyright retains the distinction.
            query = cast("LiteralString", statement.replace("?", "%s"))  # type: ignore[redundant-cast]
            return self._connection.execute(sql.SQL(query), parameters)

    def close(self) -> None:
        """Close this connection, discarding an uncommitted transaction.

        Returns:
            None: The driver releases its socket and any outstanding locks.
        """
        self._connection.close()


def initialize(database: Postgres) -> None:
    """Apply the initial PostgreSQL schema under the shared migration lock.

    Args:
        database (Postgres): Open connection owned and closed by the caller.

    Returns:
        None: Version six exists and no stored order or delivery state is rewritten.

    Raises:
        RuntimeError: Schema statements or transaction completion fail.
        ValueError: An existing PostgreSQL schema version is unsupported.
    """  # noqa: DOC503 - Driver failures propagate.
    with database:
        database.execute("BEGIN IMMEDIATE")
        database.execute(
            "CREATE TABLE IF NOT EXISTS drawer_schema "
            "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
            "version INTEGER NOT NULL)"
        )
        row = database.execute(
            "SELECT version FROM drawer_schema WHERE singleton=1"
        ).fetchone()
        if row is not None:
            if row["version"] != SCHEMA_VERSION:
                message = "Unsupported PostgreSQL schema; update Drawer Assistant."
                raise ValueError(message)
            return
        declarations = (
            SCHEMA.split("PRAGMA", maxsplit=1)[0],
            ALIBABA_SCHEMA,
            ASSETS_SCHEMA.replace("BLOB", "BYTEA"),
            REFERENCE_FILES_SCHEMA,
            (
                "ALTER TABLE orders ADD COLUMN brief TEXT NOT NULL DEFAULT '';"
                "ALTER TABLE orders ADD COLUMN comments TEXT NOT NULL DEFAULT '';"
                "ALTER TABLE orders ADD COLUMN priority INTEGER NOT NULL DEFAULT 2 "
                "CHECK(priority BETWEEN 1 AND 3)"
            ),
            STAGE_SCHEMA.replace(
                "REFERENCES order_stages(order_id,id)",
                "REFERENCES order_stages(order_id,id) DEFERRABLE INITIALLY DEFERRED",
            ),
            BATCH_SCHEMA,
        )
        for declaration in declarations:
            for statement in declaration.split(";"):
                if statement.strip():
                    database.execute(statement)
        database.execute(
            "INSERT INTO drawer_schema(singleton,version) VALUES(1,?)",
            (SCHEMA_VERSION,),
        )


def connect_postgres(url: str) -> Postgres:
    """Open and initialize a versioned PostgreSQL schema without rewriting records.

    Args:
        url (str): PostgreSQL URL accepted by Postgres, including TLS settings.

    Returns:
        Postgres: Open, initialized connection; caller must close it.

    Raises:
        RuntimeError: Connecting, schema statements or transaction completion fail.
        ValueError: URL or an existing schema version is unsupported.
    """  # noqa: DOC503 - Driver and URL errors propagate.
    database = Postgres(url)
    try:
        initialize(database)
    except BaseException:
        database.close()
        raise
    return database
