# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Build read-only order cards and factual seven-day review snapshots."""

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from .references import METADATA
from .stages import stage_rows, stage_tree
from .store import read_orders, row_values, transaction
from .time_display import TIMEZONE, local_time

if TYPE_CHECKING:
    from pathlib import Path

PRIORITIES = {"1": "низкий", "2": "средний", "3": "высокий"}
# The exact ornamental separator was supplied by the user.
SEPARATOR = "୨୧ ───────────── ୨୧"
STATUS_LABELS = {
    "open": "в работе",
    "pending": "запланирован",
    "in_progress": "в работе",
    "blocked": "ожидает решения",
    "done": "завершён",
    "cancelled": "отменён",
}


def markdown(value: object) -> str:
    """Escape stored user text before inserting it into a Markdown card.

    Args:
        value (object): Stored field value rendered as text.

    Returns:
        str: Text with Markdown metacharacters escaped.
    """
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", str(value))


def describe_stages(tree: list[dict[str, object]], depth: int = 0) -> list[str]:
    """Render nested stages, comments, completion dates and saved image identifiers.

    Args:
        tree (list[dict[str, object]]): Typed internal stage_tree result.
        depth (int, default=0): Indentation level for nested stages.

    Returns:
        list[str]: Markdown-safe lines in parent/child order.

    Raises:
        KeyError: Internal stage metadata lacks a required field or status.
        ValueError: A stored timestamp cannot be formatted.
        OverflowError: Date conversion exceeds datetime bounds.
    """  # noqa: DOC502 - Recursive formatting and lookups can fail.
    lines: list[str] = []
    for stage in tree:
        prefix = "  " * depth
        lines.append(
            f"{prefix}• {markdown(stage['title'])} — "
            f"{STATUS_LABELS[str(stage['status'])]}"
        )
        for field, label in (("description", "Описание"), ("comments", "Комментарии")):
            if stage[field]:
                lines.append(f"{prefix}  {label}: {markdown(stage[field])}")
        for field, label in (("due", "Срок"), ("completed_at", "Завершён")):
            if stage[field]:
                lines.append(f"{prefix}  {label}: {local_time(str(stage[field]))}")
        # stage_tree produces these exact nested lists; no untyped external JSON here.
        lines.extend(
            f"{prefix}  🖼 {markdown(image['title'])}: #{markdown(image['id'])}"
            for image in cast("list[dict[str, str]]", stage["images"])
        )
        lines.extend(
            describe_stages(
                cast("list[dict[str, object]]", stage["children"]),
                depth + 1,
            )
        )
    return lines


def order_info(path: Path, identifier: str) -> dict[str, object]:
    """Read an entire order and return its ready-to-send Jessica card.

    Args:
        path (Path): Artist database filename.
        identifier (str): Existing order id, never inferred from conversation memory.

    Returns:
        dict[str, object]: Markdown text and structured order with stages/references.

    Raises:
        KeyError: The order or required persisted field is missing.
        ValueError: Schema, dates or stage structure is invalid.
        OSError: The database directory cannot be accessed.
        sqlite3.Error: Reading or committing the snapshot fails.
        OverflowError: Date conversion exceeds datetime bounds.
    """  # noqa: DOC503 - Storage and formatting errors propagate.
    with transaction(path) as database:
        database.execute("BEGIN")
        row = database.execute(
            "SELECT * FROM orders WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise KeyError(identifier)
        order = row_values(row)
        tree = stage_tree(database, identifier)
        refs = [
            row_values(item)
            for item in database.execute(
                METADATA + " WHERE refs.order_id=? ORDER BY refs.id",
                (identifier,),
            )
        ]
    body = [
        f"Статус: {STATUS_LABELS[order['status']]}",
        f"Приоритет: {order['priority']} — {PRIORITIES[order['priority']]}",
    ]
    for field, label in (
        ("client", "Клиент"),
        ("brief", "ТЗ"),  # noqa: RUF001 - Russian abbreviation for the specification.
        ("comments", "Комментарии"),
        ("notes", "Заметки"),
    ):
        if order[field]:
            body.append(f"{label}: {markdown(order[field])}")
    if tree:
        body.extend(["Этапы:", *describe_stages(tree)])
    if refs:
        body.append("Референсы:")
        body.extend(
            f"• {markdown(ref['title'])} — #{markdown(ref['id'])} "
            f"({'файл сохранён' if ref['storage'] == 'blob' else 'только ссылка'})"
            for ref in refs
        )
    text = (
        "🎀 **Джессика на связи!** ♡\n"
        f"✨ **{markdown(order['title'])}**\n"
        f"🕰 **Дедлайн:** {local_time(order['due'])}\n"
        f"💌 **Заказик:** #{markdown(identifier)}\n"
        f"{SEPARATOR}\n"
        + "\n".join(body)
        + f"\n{SEPARATOR}\n"
        + "💗 С любовью и заботой,\nтвоя Джессика ♡"  # noqa: RUF001 - Exact Russian signature.
    )
    return {"text": text, "order": {**order, "stages": tree, "references": refs}}


def order_digest(path: Path, now: datetime | None = None) -> dict[str, object]:
    """Read overdue and upcoming orders and suggest priorities without writing them.

    Args:
        path (Path): Artist database filename.
        now (datetime | None, default=None): Aware window start; None uses current UTC.

    Returns:
        dict[str, object]: Seven-day active/completed orders, undated active orders,
            unfinished stages and suggested priorities; original priorities retained.

    Raises:
        ValueError: The clock is naive, schema is invalid or a timestamp is malformed.
        KeyError: Required persisted fields are absent.
        OSError: Database directory cannot be accessed.
        sqlite3.Error: Reading or committing the snapshot fails.
        OverflowError: Date arithmetic exceeds datetime bounds.
    """  # noqa: DOC503 - Storage, arithmetic and parsing errors propagate.
    moment = now if now is not None else datetime.now(UTC)
    if moment.tzinfo is None:
        message = "Review clock must include a UTC offset."
        raise ValueError(message)
    moment = moment.astimezone(UTC)
    until = moment + timedelta(days=7)
    active: list[dict[str, object]] = []
    completed: list[dict[str, str]] = []
    undated: list[dict[str, str]] = []
    with transaction(path) as database:
        database.execute("BEGIN")
        for order in read_orders(database):
            if not order["due"]:
                if order["status"] == "open":
                    undated.append(order)
                continue
            due = datetime.fromisoformat(order["due"])
            if due > until or order["status"] == "cancelled":
                continue
            if order["status"] == "done":
                if due >= moment:
                    completed.append(order)
                continue
            pending = [
                stage
                for stage in stage_rows(database, order["id"])
                if stage["status"] not in {"done", "cancelled"}
            ]
            urgent = due <= moment + timedelta(days=2)
            reason = "Срок просрочен" if due < moment else "До срока не более двух дней"
            active.append(
                {
                    **order,
                    "deadline_local": local_time(order["due"]),
                    "overdue": due < moment,
                    "unfinished_stages": pending,
                    "stages": stage_tree(database, order["id"]),
                    "suggested_priority": 3 if urgent else int(order["priority"]),
                    "priority_reason": reason
                    if urgent
                    else "Сохранить текущий приоритет",
                }
            )
    return {
        "generated_at": local_time(moment.isoformat()),
        "until": local_time(until.isoformat()),
        "timezone": TIMEZONE,
        "orders": active,
        "completed": completed,
        "without_deadline": undated,
    }
