# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Verify binary durability, migrations, deduplication and transactional failures."""

import base64
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock

import pytest

from drawer_assistant import reference_files
from drawer_assistant.plugin import handle
from drawer_assistant.references import references
from drawer_assistant.store import ALIBABA_SCHEMA, SCHEMA, orders, transaction

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8A"
    "AwMCAO+aL1kAAAAASUVORK5CYII="
)


def test_binary_survives_cache_and_export_deletion(tmp_path: Path) -> None:
    """Restore identical bytes without the original attachment or an earlier export.

    Args:
        tmp_path (Path): Isolated directory for a simulated Telegram cache and database.

    Returns:
        None: Reopened SQLite contains bytes and restores them with order metadata.

    Raises:
        AssertionError: Bytes, relation, metadata or response serialization is wrong.
        OSError: Test files or exports cannot be accessed.
        ValueError: Fixture arguments, integrity or schema are invalid.
        KeyError: A required field or timezone is missing.
        RuntimeError: An unexpected image download fails.
        sqlite3.Error: Test database operations fail.
        OverflowError: Order date normalization fails.
    """  # noqa: DOC502 - Fixture calls and assertions can raise.
    path = tmp_path / "artist.db"
    photo = tmp_path / "поза из Telegram.png"
    photo.write_bytes(PNG)
    orders(path, {"action": "create", "id": "halloween", "title": "Halloween"})
    saved = references(
        path,
        {
            "action": "save",
            "id": "pose",
            "title": "Поза",
            "order_id": "halloween",
            "notes": "Взять позу, не палитру",
            "file": str(photo),
        },
    )[0]
    assert saved["storage"] == "blob"
    assert saved["size_bytes"] == str(len(PNG))
    assert saved["sha256"] == hashlib.sha256(PNG).hexdigest()
    assert saved["filename"] == photo.name
    assert "data" not in saved
    assert "file" not in saved
    photo.unlink()
    for _ in range(2):
        retrieved = references(path, {"action": "get", "id": "pose"})[0]
        export = Path(retrieved["file"])
        assert export.read_bytes() == PNG
        assert retrieved["media"] == f"MEDIA:{export}"
        assert retrieved["order_id"] == "halloween"
        assert retrieved["notes"] == saved["notes"]
        export.unlink()
    assert references(path, {"action": "list", "order_id": "halloween"}) == [saved]
    with transaction(path) as database:
        row = database.execute("SELECT typeof(data),data FROM assets").fetchone()
        assert row[0] == "blob"
        assert row[1] == PNG


def test_deduplication_metadata_updates_and_replacement(tmp_path: Path) -> None:
    """Share bytes across references without losing an attachment on metadata edits.

    Args:
        tmp_path (Path): Isolated files and SQLite directory.

    Returns:
        None: Shared binaries survive replacement until their last reference changes.

    Raises:
        AssertionError: Deduplication, retention or replacement behaves incorrectly.
        OSError: Image files or database directory cannot be accessed.
        ValueError: Test input or schema is invalid.
        KeyError: Expected fixture or result field is missing.
        RuntimeError: An unexpected download fails.
        sqlite3.Error: Database operations fail.
    """  # noqa: DOC502 - Delegated fixture and assertion errors propagate.
    path, photo = tmp_path / "artist.db", tmp_path / "image.png"
    photo.write_bytes(PNG)
    for identifier in ("first", "second"):
        references(
            path,
            {
                "action": "save",
                "id": identifier,
                "title": identifier,
                "file": str(photo),
            },
        )
    references(path, {"action": "save", "id": "first", "notes": "Only lighting"})
    with transaction(path) as database:
        assert database.execute("SELECT count(*) FROM assets").fetchone()[0] == 1
    photo.unlink()
    assert references(path, {"action": "get", "id": "first"})[0]["storage"] == "blob"
    replacement = b"GIF89a" + bytes(20)
    photo.write_bytes(replacement)
    for identifier in ("first", "second"):
        references(path, {"action": "save", "id": identifier, "file": str(photo)})
        if identifier == "first":
            other = references(path, {"action": "get", "id": "second"})[0]
            assert Path(other["file"]).read_bytes() == PNG
    with transaction(path) as database:
        assert database.execute("SELECT count(*) FROM assets").fetchone()[0] == 1
    result = references(path, {"action": "get", "id": "first"})[0]
    assert Path(result["file"]).read_bytes() == replacement
    assert result["notes"] == "Only lighting"


@pytest.mark.parametrize("version", [1, 2, 3])
def test_migrate_legacy_links(tmp_path: Path, version: int) -> None:
    """Keep historical links honest and add a binary later without losing notes.

    Args:
        tmp_path (Path): Isolated database and image directory.
        version (int): Historical schema version to migrate from, 1 through 3.

    Returns:
        None: Old links remain link_only until an image is actually imported.

    Raises:
        AssertionError: Migration loses data or claims a nonexistent binary.
        OSError: Fixture files or database directory cannot be accessed.
        ValueError: Arguments or database schema are invalid.
        KeyError: An expected fixture field is missing.
        RuntimeError: An unexpected image download fails.
        sqlite3.Error: Legacy schema creation or migration fails.
    """  # noqa: DOC502 - Fixture persistence and assertions can fail.
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as database:
        database.executescript(SCHEMA)
        if version == 1:
            database.execute("ALTER TABLE jobs DROP COLUMN generation_seconds")
        alibaba_version = 3
        if version == alibaba_version:
            database.execute(ALIBABA_SCHEMA)
        database.execute(f"PRAGMA user_version = {version}")
        database.execute(
            "INSERT INTO refs VALUES('old','Pose','https://example.com',"
            "'','Take the palette')"
        )
        database.commit()
    old = references(path, {"action": "get", "id": "old"})[0]
    assert old["storage"] == "link_only"
    assert old["size_bytes"] == "0"
    assert "file" not in old
    photo = tmp_path / "palette.png"
    photo.write_bytes(PNG)
    saved = references(path, {"action": "save", "id": "old", "file": str(photo)})[0]
    assert saved["storage"] == "blob"
    assert saved["notes"] == "Take the palette"
    with transaction(path) as database:
        current = 4
        assert database.execute("PRAGMA user_version").fetchone()[0] == current
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_reference_download_and_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download only explicit image URLs and reject invalid input without corruption.

    Args:
        tmp_path (Path): Isolated reference database and local fixture directory.
        monkeypatch (pytest.MonkeyPatch): Replaces downloads and the image size limit.

    Returns:
        None: URL bytes are stored; failed replacements preserve the original record.

    Raises:
        AssertionError: Downloads, error boundaries or retained data are incorrect.
        OSError: Fixture files or database directory cannot be accessed.
        ValueError: An unexpected fixture or schema validation fails.
        KeyError: Expected fields are missing outside the missing-order test.
        RuntimeError: An unexpected network failure escapes its expected test.
        sqlite3.Error: Test database operations fail.
    """  # noqa: DOC502 - Fixture calls and assertions propagate errors.
    path = tmp_path / "references.db"
    download = Mock(return_value=PNG)
    monkeypatch.setattr(reference_files, "request", download)
    params = {
        "action": "save",
        "id": "remote",
        "title": "Palette",
        "image_url": "https://example.com/palette.png",
    }
    saved = references(path, params)
    assert saved[0]["url"] == params["image_url"]
    download.assert_called_once_with(params["image_url"])
    with pytest.raises(KeyError):
        references(path, {**params, "order_id": "missing"})
    download.return_value = b"<html>Not an image</html>"
    with pytest.raises(ValueError, match="PNG/JPEG"):
        references(path, params)
    download.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError, match="offline"):
        references(path, params)
    with pytest.raises(ValueError, match="either"):
        references(path, {**params, "file": str(tmp_path / "anything.png")})
    with pytest.raises(ValueError, match="absolute"):
        references(path, {"action": "save", "id": "relative", "file": "relative.png"})
    photo = tmp_path / "large.png"
    photo.write_bytes(PNG)
    monkeypatch.setattr(reference_files, "MAX_IMAGE", len(PNG) - 1)
    with pytest.raises(ValueError, match="20 MiB"):
        references(path, {"action": "save", "id": "remote", "file": str(photo)})
    assert references(path, {"action": "list"}) == saved


def test_binary_transaction_and_integrity(tmp_path: Path) -> None:
    """Roll back metadata when binary insertion fails and never export corrupted bytes.

    Args:
        tmp_path (Path): Isolated plugin settings, image and SQLite directory.

    Returns:
        None: Storage failures are atomic and tool errors contain no binary contents.

    Raises:
        AssertionError: Failed writes leak metadata or corrupted bytes are exported.
        OSError: Fixture files cannot be written.
        ValueError: Unexpected fixture input or schema validation fails.
        KeyError: A required fixture field is missing.
        RuntimeError: An unexpected image download fails.
        sqlite3.Error: Fixture database operations fail outside the forced error.
    """  # noqa: DOC502 - Fixture calls and assertions can raise.
    path, photo = tmp_path / "artist.db", tmp_path / "image.png"
    photo.write_bytes(PNG)
    params = {"action": "save", "id": "test", "title": "Before", "file": str(photo)}
    references(path, params)
    with transaction(path) as database:
        database.execute(
            "CREATE TRIGGER reject_blob BEFORE INSERT ON assets BEGIN "
            "SELECT RAISE(ABORT, 'disk failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError, match="disk failure"):
        references(path, {**params, "title": "After"})
    assert references(path, {"action": "list"})[0]["title"] == "Before"
    with transaction(path) as database:
        database.execute("UPDATE assets SET data=?", (b"corrupt binary",))
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"database": str(path), "comfy_url": "http://test"}), "utf-8"
    )
    result = handle(
        {"action": "get", "id": "test"},
        tool="drawer_references",
        settings_file=settings,
    )
    assert '"error": "ValueError"' in result
    assert "corrupt binary" not in result
    assert not (tmp_path / "reference-exports").exists()
