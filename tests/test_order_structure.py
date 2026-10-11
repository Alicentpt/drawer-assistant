# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Verify durable nested orders, safe migration, cards and review boundaries."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from drawer_assistant.installation import cron_command
from drawer_assistant.order_views import order_digest, order_info
from drawer_assistant.references import references
from drawer_assistant.stages import stages
from drawer_assistant.store import (
    ALIBABA_SCHEMA,
    ASSETS_SCHEMA,
    REFERENCE_FILES_SCHEMA,
    SCHEMA,
    orders,
    transaction,
)
from drawer_assistant.time_display import local_time

if TYPE_CHECKING:
    from pathlib import Path


def test_order_migration(tmp_path: Path) -> None:
    """Keep deadline instants and delivery state while migrating real v4 fields.

    Args:
        tmp_path (Path): Isolated legacy database directory.

    Returns:
        None: Defaults, original notes, dates and delivery state survive reopening.

    Raises:
        AssertionError: Migration loses fields or changes actual deadline instants.
        OSError: Temporary files cannot be created.
        sqlite3.Error: The legacy database cannot be built, migrated or read.
        ValueError: A fixture schema is unsupported.
        KeyError: A required migrated field is absent.
    """  # noqa: DOC502 - Storage and assertion exceptions propagate.
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as database:
        database.executescript(SCHEMA)
        for statement in (ALIBABA_SCHEMA, ASSETS_SCHEMA, REFERENCE_FILES_SCHEMA):
            database.execute(statement)
        database.execute("PRAGMA user_version=4")
        database.execute(
            "INSERT INTO orders(id,title,client,notes,status,due,timezone,remind_at,"
            "sent_at,attempts) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                "legacy",
                "Old",
                "Client",
                "Original brief",
                "open",
                "2026-10-31T15:00:00+00:00",
                "Europe/Kaliningrad",
                "2026-10-30T09:00:00+00:00",
                "2026-10-30T09:01:00+00:00",
                1,
            ),
        )
        database.commit()
    first = orders(path, {"action": "list"})[0]
    assert first["timezone"] == "Europe/Moscow"
    assert first["due"] == "2026-10-31T15:00:00+00:00"
    assert first["remind_at"] == "2026-10-30T09:00:00+00:00"
    assert first["sent_at"] == "2026-10-30T09:01:00+00:00"
    assert first["attempts"] == "1"
    assert first["notes"] == "Original brief"
    assert (first["brief"], first["comments"], first["priority"]) == ("", "", "2")
    assert orders(path, {"action": "list"}) == [first]
    with transaction(path) as database:
        assert database.execute("PRAGMA foreign_key_check").fetchall() == []


def test_nested_results_and_card(tmp_path: Path) -> None:
    """Keep finished illustrated stages inside one order and render the requested card.

    Args:
        tmp_path (Path): Isolated order database and reference image directory.

    Returns:
        None: Stages, completion history, binary links and escaped card are verified.

    Raises:
        AssertionError: Nested results are lost, duplicated or incorrectly rendered.
        OSError: Fixture files or the database cannot be accessed.
        sqlite3.Error: Stage or reference persistence fails.
        ValueError: A fixture date, image, status or schema is invalid.
        KeyError: A required fixture order, stage or field is missing.
        OverflowError: Fixture date conversion exceeds bounds.
        RuntimeError: Reference transport unexpectedly fails.
    """  # noqa: DOC502 - Fixture storage and formatting errors propagate.
    path = tmp_path / "orders.db"
    orders(
        path,
        {
            "action": "create",
            "id": "halloween",
            "title": "Witches *pair*",
            "brief": "Two adult witches, purple palette",
            "comments": "Client approved",
            "priority": "3",
            "due": "2026-10-31T18:00:00+03:00",
        },
    )
    root = {"action": "create", "order_id": "halloween", "id": "art", "title": "Art"}
    stages(path, root)
    child = {
        **root,
        "id": "sketch",
        "parent_id": "art",
        "title": "Sketch",
        "status": "done",
        "description": "Initial sketch",
        "comments": "Please adjust the sleeve",
    }
    tree = stages(path, child)
    assert stages(path, child) == tree
    image = tmp_path / "sketch.png"
    # A bounded PNG fixture suffices: production reference validation checks magic.
    image.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fixture")
    references(
        path,
        {
            "action": "save",
            "id": "sketch-img",
            "title": "Sketch result",
            "order_id": "halloween",
            "file": str(image),
        },
    )
    attachment = {
        "action": "attach",
        "order_id": "halloween",
        "id": "sketch",
        "reference_id": "sketch-img",
    }
    tree = stages(path, attachment)
    assert stages(path, attachment) == tree
    stages(path, {**child, "action": "update", "comments": "Sleeve fixed"})
    card = order_info(path, "halloween")
    text = str(card["text"])
    assert text.startswith("🎀 **Witches \\*pair\\***\n\n**Дедлайн**\n")
    assert "31.10.2026 6:00 PM" in text
    assert "Заказ: #halloween" in text
    assert "୨୧" not in text
    assert "\n\n\n" not in text
    assert "Two adult witches, purple palette" in text
    assert "3 — высокий" in text
    assert "Sleeve fixed" in text
    assert "Файл сохранён" in text
    assert text.rsplit("\n\n", maxsplit=1)[-1] == "Джессика ♡"
    assert len(orders(path, {"action": "list"})) == 1
    with transaction(path) as database:
        saved = database.execute(
            "SELECT completed_at FROM order_stages WHERE id='sketch'"
        ).fetchone()
        assert saved[0]
        assert database.execute("SELECT count(*) FROM stage_images").fetchone()[0] == 1
        assert (
            database.execute("SELECT data FROM assets").fetchone()[0]
            == image.read_bytes()
        )


def test_stage_parent_rollback(tmp_path: Path) -> None:
    """Reject cyclic and foreign parents without losing existing nested stages.

    Args:
        tmp_path (Path): Isolated order database directory.

    Returns:
        None: Invalid parent changes and link-only attachments leave data intact.

    Raises:
        AssertionError: A rejected mutation changes the tree.
        OSError: The database directory cannot be created.
        sqlite3.Error: Fixture persistence fails outside expected validation.
        ValueError: A fixture order or stage is invalid.
        KeyError: A fixture field or order is missing.
        OverflowError: Fixture timestamp conversion fails.
        RuntimeError: Reference transport unexpectedly fails.
    """  # noqa: DOC502 - Delegated storage and assertions can raise.
    path = tmp_path / "orders.db"
    for identifier in ("one", "two"):
        orders(path, {"action": "create", "id": identifier, "title": identifier})
    root = {"action": "create", "order_id": "one", "id": "root", "title": "Root"}
    stages(path, root)
    original = stages(path, {**root, "id": "child", "parent_id": "root"})
    with pytest.raises(ValueError, match="acyclic"):
        stages(path, {**root, "action": "update", "parent_id": "child"})
    with pytest.raises(KeyError):
        stages(path, {**root, "order_id": "two", "id": "other", "parent_id": "root"})
    references(
        path,
        {
            "action": "save",
            "id": "link",
            "title": "Link",
            "order_id": "one",
            "url": "https://example.com/ref.png",
        },
    )
    with pytest.raises(ValueError, match="actual image"):
        stages(path, {**root, "action": "attach", "reference_id": "link"})
    assert stages(path, {"action": "list", "order_id": "one"}) == original
    assert not stages(path, {"action": "list", "order_id": "two"})
    with pytest.raises(ValueError, match="Priority"):
        orders(path, {"action": "update", "id": "one", "priority": "4"})


def test_review_is_read_only(tmp_path: Path) -> None:
    """Include overdue and seven-day work, excluding cancelled and later deadlines.

    Args:
        tmp_path (Path): Isolated order database directory.

    Returns:
        None: Review boundaries, unfinished stages and unchanged priorities are checked.

    Raises:
        AssertionError: Review invents work, misses deadlines or edits priorities.
        OSError: The database cannot be accessed.
        sqlite3.Error: Fixture storage or review queries fail.
        ValueError: Fixture dates or schema are invalid.
        KeyError: A fixture field, order or stage is missing.
        OverflowError: Date calculations exceed bounds.
    """  # noqa: DOC502 - Storage, formatting and assertions can raise.
    path = tmp_path / "orders.db"
    for identifier, due, status in (
        ("overdue", "2026-10-10T12:00:00+03:00", "open"),
        ("soon", "2026-10-12T12:00:00+03:00", "open"),
        ("boundary", "2026-10-18T12:00:00+03:00", "open"),
        ("later", "2026-10-18T12:00:01+03:00", "open"),
        ("finished", "2026-10-12T12:00:00+03:00", "done"),
        ("cancelled", "2026-10-12T12:00:00+03:00", "cancelled"),
        ("undated", "", "open"),
    ):
        orders(
            path,
            {
                "action": "create",
                "id": identifier,
                "title": identifier,
                "due": due,
                "status": status,
                "priority": "1",
            },
        )
    for identifier, status in (("sketch", "done"), ("final", "blocked")):
        stages(
            path,
            {
                "action": "create",
                "order_id": "soon",
                "id": identifier,
                "title": identifier,
                "status": status,
            },
        )
    before = orders(path, {"action": "list"})
    snapshot = order_digest(path, datetime(2026, 10, 11, 9, tzinfo=UTC))
    # JSON gives an oracle independent of internal collection types.
    text = json.dumps(snapshot, ensure_ascii=False)
    assert '"id": "overdue"' in text
    assert '"id": "boundary"' in text
    assert '"id": "later"' not in text
    assert '"id": "cancelled"' not in text
    assert '"suggested_priority": 3' in text
    assert '"priority": "1"' in text
    assert '"overdue": true' in text
    assert '"without_deadline": [{"id": "undated"' in text
    assert '"completed": [{"id": "finished"' in text
    assert '"unfinished_stages": [{"order_id": "soon", "id": "final"' in text
    assert orders(path, {"action": "list"}) == before
    with pytest.raises(ValueError, match="UTC offset"):
        # Deliberately naive: this is the invalid-input case under test.
        order_digest(path, datetime(2026, 10, 11))  # noqa: DTZ001


def test_native_cron_selection(tmp_path: Path) -> None:
    """Update one native reminder cron and create a separate daily review cron.

    Args:
        tmp_path (Path): Isolated Hermes profile directory.

    Returns:
        None: Native commands use the requested schedules without duplicate jobs.

    Raises:
        AssertionError: Command selection or schedule is wrong.
        OSError: Fixture cron metadata cannot be created or read.
        UnicodeError: Fixture text cannot be encoded or decoded.
        ValueError: Fixture cron JSON is malformed.
        TypeError: Fixture jobs are not a list.
        KeyError: Fixture metadata lacks an id.
    """  # noqa: DOC502 - Native metadata reading and assertions can raise.
    cron = tmp_path / "cron"
    cron.mkdir()
    metadata = cron / "jobs.json"
    metadata.write_text(
        json.dumps({"jobs": [{"id": "old", "name": "drawer-reminders"}]}), "utf-8"
    )
    assert cron_command(tmp_path, "drawer-reminders", "every 30m") == [
        "cron",
        "edit",
        "old",
        "--schedule",
        "every 30m",
        "--name",
        "drawer-reminders",
    ]
    assert cron_command(tmp_path, "drawer-order-review", "0 12 * * *", "Review") == [
        "cron",
        "create",
        "0 12 * * *",
        "Review",
        "--name",
        "drawer-order-review",
    ]
    assert cron_command(tmp_path, "drawer-reminders", "every 30m", "Read") == [
        "cron",
        "edit",
        "old",
        "--schedule",
        "every 30m",
        "--prompt",
        "Read",
        "--name",
        "drawer-reminders",
    ]
    metadata.write_text(
        json.dumps(
            {
                "jobs": [
                    {"id": "a", "name": "drawer-reminders"},
                    {"id": "b", "name": "drawer-reminders"},
                ]
            }
        ),
        "utf-8",
    )
    with pytest.raises(ValueError, match="Multiple"):
        cron_command(tmp_path, "drawer-reminders", "every 30m")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-10T21:00:00+00:00", "11.10.2026 12:00 AM"),
        ("2026-10-11T09:00:00+00:00", "11.10.2026 12:00 PM"),
        ("2026-10-11T15:30:00+00:00", "11.10.2026 6:30 PM"),
    ],
)
def test_twelve_hour_clock(value: str, expected: str) -> None:
    """Display midnight, noon and evening in Moscow independently of OS locale.

    Args:
        value (str): Aware UTC timestamp to display.
        expected (str): Expected date and twelve-hour Moscow clock substring.

    Returns:
        None: Midnight and noon remain distinguishable and naive dates are rejected.

    Raises:
        AssertionError: A timestamp has the wrong date, clock or AM/PM suffix.
        ValueError: A valid fixture timestamp is rejected.
        KeyError: Moscow timezone data is missing.
        OverflowError: Fixture date conversion exceeds bounds.
    """  # noqa: DOC502 - Formatting and assertion exceptions propagate.
    assert expected in local_time(value)
    assert local_time("") == "не задан"
    with pytest.raises(ValueError, match="UTC offset"):
        local_time("2026-10-11T12:00:00")
