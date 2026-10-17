# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Exercise the real PostgreSQL backend in disposable schemas, including migration."""

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from drawer_assistant.database import Database, configured_database
from drawer_assistant.generation import jobs
from drawer_assistant.migration import snapshot, transfer
from drawer_assistant.order_views import order_info
from drawer_assistant.references import references
from drawer_assistant.reminders import deliver_due
from drawer_assistant.stages import stages
from drawer_assistant.store import orders, transaction

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(name="postgres_database")
def postgres_target(tmp_path: Path) -> Generator[Database]:
    """Create an isolated schema without touching pre-existing application tables.

    Args:
        tmp_path (Path): Local cache anchor for this one test.

    Yields:
        Database: PostgreSQL target whose unique schema is dropped after the test.

    Raises:
        psycopg.Error: The explicitly configured test server is unavailable.
        ValueError: The test database URL is invalid.
    """  # noqa: DOC502 - Driver and URL parsing failures propagate.
    url = os.environ.get("DRAWER_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("Set DRAWER_TEST_DATABASE_URL to run live PostgreSQL tests")
    schema = "drawer_test_" + uuid4().hex
    parts = urlsplit(url)
    query = dict(parse_qsl(parts.query))
    query["options"] = query.get("options", "") + f" -csearch_path={schema}"
    scoped_url = urlunsplit(parts._replace(query=urlencode(query, quote_via=quote)))
    with psycopg.connect(url, autocommit=True) as administrator:
        administrator.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema))
        )
        try:
            yield Database(tmp_path / "cache.sqlite3", scoped_url)
        finally:
            administrator.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


def populate(path: Path | Database, directory: Path) -> bytes:
    """Build nested stages, a binary reference and durable generation/delivery data.

    Args:
        path (Path | Database): Selected storage backend for the fixture.
        directory (Path): Directory for the simulated Telegram attachment.

    Returns:
        bytes: Image bytes expected to survive migration and export.

    Raises:
        OSError: The attachment or local database cannot be written.
        ValueError: Fixture fields, timestamps or schema are invalid.
        KeyError: A required order, stage or image is missing.
        RuntimeError: PostgreSQL operations or image transport fails.
        sqlite3.Error: SQLite fixture operations fail.
        OverflowError: Fixture timestamps exceed supported bounds.
    """  # noqa: DOC502 - Application operations raise indirectly.
    orders(
        path,
        {
            "action": "create",
            "id": "art",
            "title": "Заказ 🖤",
            "priority": "3",
            "brief": "Клиентка: иллюстрация",
            "comments": "Комментарии сохраняются",
            "due": "2026-10-31T18:00:00+03:00",
        },
    )
    root = {"action": "create", "order_id": "art", "id": "z-parent", "title": "Работа"}
    stages(path, root)
    stages(
        path,
        {
            **root,
            "id": "a-child",
            "title": "Эскиз",
            "parent_id": "z-parent",
            "status": "done",
            "comments": "Правки приняты",
        },
    )
    data = b"\x89PNG\r\n\x1a\n" + b"binary-reference-fixture"
    image = directory / "reference.png"
    image.write_bytes(data)
    references(
        path,
        {
            "action": "save",
            "id": "ref",
            "title": "Эскиз",
            "order_id": "art",
            "file": str(image),
        },
    )
    stages(
        path,
        {"action": "attach", "order_id": "art", "id": "a-child", "reference_id": "ref"},
    )
    with transaction(path) as database:
        database.execute(
            "INSERT INTO jobs(id,prompt,workflow,prompt_id,state) VALUES(?,?,?,?,?)",
            (
                "gpu-job",
                "A woman, what's next? 50%",
                "nova-anime-am-v20",
                "ack",
                "queued",
            ),
        )
        database.execute(
            "INSERT INTO image_batches(id,spec) VALUES(?,?)",
            ("sheet", '{"prompts":["one","two"]}'),
        )
        database.execute(
            "INSERT INTO alibaba_jobs(id,model,base,fingerprint,request) "
            "VALUES(?,?,?,?,?)",
            ("legacy", "old-model", "https://example.invalid", "hash", "{}"),
        )
        database.execute(
            "UPDATE orders SET sent_at=?,attempts=2,next_attempt=?,"
            "last_error='delivery_failed' WHERE id='art'",
            ("2026-10-01T12:00:00+00:00", "2026-10-02T12:00:00+00:00"),
        )
    return data


def test_profile_database_isolation(tmp_path: Path) -> None:
    """Resolve each profile independently and keep credentials out of repr.

    Args:
        tmp_path (Path): Isolated profile tree.

    Returns:
        None: URL selection and local cache locations are verified.

    Raises:
        OSError: Temporary files cannot be written.
        AssertionError: A profile leaks credentials or selects another profile's URL.
    """  # noqa: DOC502 - Temporary filesystem operations can fail.
    settings = tmp_path / "profile/plugins/drawer/settings.json"
    settings.parent.mkdir(parents=True)
    local = tmp_path / "cache.sqlite3"
    payload: dict[str, object] = {"database": str(local)}
    assert configured_database(settings, payload) == local
    (settings.parents[2] / ".env").write_text(
        "DRAWER_DATABASE_URL=postgresql://user:private-password@localhost/drawer\n"
    )
    target = configured_database(settings, payload)
    assert isinstance(target, Database)
    assert target.parent == tmp_path
    assert "private-password" not in repr(target)


def test_snapshot_copy_integrity(tmp_path: Path, postgres_database: Database) -> None:
    """Round-trip all tables, nested links, Unicode, bytea and delivery state.

    Args:
        tmp_path (Path): SQLite source, snapshot and binary export directory.
        postgres_database (Database): Isolated live PostgreSQL schema.

    Returns:
        None: Full fingerprints, idempotent copy and conflict refusal are verified.

    Raises:
        OSError: Fixture, backup or export files cannot be accessed.
        sqlite3.Error: SQLite operations fail unexpectedly.
        ValueError: A fixture or migration is invalid outside expected refusal.
        KeyError: A required fixture record is missing.
        RuntimeError: PostgreSQL operations or reference transport fail.
        AssertionError: Migrated data, hashes or non-destructive behavior differ.
        OverflowError: Fixture timestamps cannot be normalized.
        TypeError: Stored fixture fields cannot be serialized.
    """  # noqa: DOC502 - Fixture setup and database operations can fail.
    source = tmp_path / "source.sqlite3"
    data = populate(source, tmp_path)
    backup = tmp_path / "snapshot.sqlite3"
    snapshot(source, backup)
    original_bytes = backup.read_bytes()
    result = transfer(backup, postgres_database.url, apply=True)
    assert transfer(backup, postgres_database.url) == result
    assert transfer(backup, postgres_database.url, apply=True) == result
    assert backup.read_bytes() == original_bytes
    restored = references(postgres_database, {"action": "get", "id": "ref"})[0]
    assert Path(restored["file"]).read_bytes() == data
    assert order_info(source, "art") == order_info(postgres_database, "art")
    orders(postgres_database, {"action": "update", "id": "art", "priority": "1"})
    with pytest.raises(ValueError, match="Destination differs"):
        transfer(backup, postgres_database.url, apply=True)
    assert orders(postgres_database, {"action": "list"})[0]["priority"] == "1"
    with pytest.raises(FileExistsError):
        snapshot(source, backup)


def test_generation_upgrade(postgres_database: Database) -> None:
    """Upgrade a simulated version-six schema without changing existing orders.

    Args:
        postgres_database (Database): Disposable uniquely named PostgreSQL schema.

    Returns:
        None: Version-seven task writes work and old order data remains intact.

    Raises:
        AssertionError: Migration changes stored orders or task persistence fails.
        ValueError: Fixture, database schema or task specification is invalid.
        OSError: Cache anchor cannot be accessed.
        RuntimeError: Disposable PostgreSQL operations fail.
        KeyError: Required fixture data is missing.
        TypeError: Fixture task cannot be serialized.
        OverflowError: Fixture date normalization fails.
        sqlite3.Error: SQLite compatibility operations fail.
    """  # noqa: DOC502 - Full storage and migration operations are exercised.
    orders(
        postgres_database, {"action": "create", "id": "retained", "title": "Keep this"}
    )
    before = orders(postgres_database, {"action": "list"})
    with transaction(postgres_database) as database:
        database.execute("DROP TABLE generation_tasks")
        database.execute("UPDATE drawer_schema SET version=6 WHERE singleton=1")
    assert jobs.reserve(
        postgres_database, "http://localhost:8188", "new", {"mode": "text"}
    )
    assert jobs.read(postgres_database, "new")["state"] == "submitting"
    assert orders(postgres_database, {"action": "list"}) == before


def test_postgres_rollback_and_reminder_lock(postgres_database: Database) -> None:
    """Rollback failed writes and serialize concurrent workers before sending.

    Args:
        postgres_database (Database): Isolated live PostgreSQL schema.

    Returns:
        None: Failed inserts disappear and two workers acknowledge one reminder.

    Raises:
        RuntimeError: PostgreSQL operations fail unexpectedly.
        ValueError: A fixture deadline is invalid outside the injected rollback.
        KeyError: A required order field is missing.
        OSError: A fallback directory cannot be created.
        AssertionError: Rollback, delivery count or priority-only edits are incorrect.
        OverflowError: Fixture time conversion fails.
    """  # noqa: DOC503 - Other errors propagate from delegated operations.
    orders(
        postgres_database,
        {
            "action": "create",
            "id": "due",
            "title": "Reminder",
            "due": "2026-10-01T12:00:00+03:00",
        },
    )
    with (  # noqa: PT012 - Inject failure after a real write to verify rollback.
        pytest.raises(ValueError, match="rollback"),
        transaction(postgres_database) as database,
    ):
        database.execute(
            "INSERT INTO refs(id,title,url,order_id,notes) VALUES(?,?,?,?,?)",
            ("rollback", "Reference", "https://example.invalid", "due", ""),
        )
        message = "rollback"
        raise ValueError(message)
    assert references(postgres_database, {"action": "list"}) == []
    sent: list[str] = []
    now = datetime(2026, 10, 2, tzinfo=UTC)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(deliver_due, postgres_database, sent.append, now)
            for _ in range(2)
        ]
        assert sum(result.result() for result in results) == 1
    assert len(sent) == 1
    before = orders(postgres_database, {"action": "list"})[0]
    after = orders(
        postgres_database, {"action": "update", "id": "due", "priority": "3"}
    )[0]
    assert after == {**before, "priority": "3"}
