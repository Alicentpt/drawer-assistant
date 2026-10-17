# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Select profile-local storage without mixing database URLs and cache paths."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import dotenv_values

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Mapping

    from psycopg.rows import DictRow

    from .postgres import Postgres

    type Connection = sqlite3.Connection | Postgres
    # SQL result columns are schema-dependent; retain the driver's dynamic row type.
    type Row = sqlite3.Row | DictRow


@dataclass(frozen=True, repr=False)
class Database:
    """Keep a remote database separate from local generated/exported files.

    Attributes:
        path (Path): Local SQLite fallback filename and cache directory anchor.
        url (str): PostgreSQL connection URL, excluded from representations.
    """

    path: Path
    url: str

    @property
    def parent(self) -> Path:
        """Locate local image caches independently of the remote database.

        Returns:
            Path: Parent directory of the local fallback database filename.
        """
        return self.path.parent


type Location = Path | Database


def configured_database(
    settings_file: Path, settings: Mapping[str, object]
) -> Location:
    """Read a database URL only from the selected Hermes profile's environment.

    Args:
        settings_file (Path): Profile/plugins/drawer/settings.json configuration.
        settings (Mapping[str, object]): Parsed settings containing database path.

    Returns:
        Location: Local SQLite path, or PostgreSQL target with the same cache anchor.

    Raises:
        OSError: The profile environment file cannot be read.
        UnicodeError: Credentials are not UTF-8.
        KeyError: The local database filename is absent.
    """  # noqa: DOC502 - Configuration errors propagate.
    credentials = dotenv_values(
        settings_file.parents[2] / ".env", interpolate=False, encoding="utf-8-sig"
    )
    path = Path(str(settings["database"]))
    url = credentials.get("DRAWER_DATABASE_URL") or ""
    return Database(path, url) if url else path
