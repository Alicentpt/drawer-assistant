# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Share readable text blocks and safe labels across artist-facing messages."""

import re
from textwrap import wrap

PRIORITIES = {"1": "низкий", "2": "средний", "3": "высокий"}
BLOCK_WIDTH = 240


def markdown(value: object) -> str:
    """Escape stored user text before inserting it into a Markdown card.

    Args:
        value (object): Stored field value rendered as text.

    Returns:
        str: Text with Markdown metacharacters escaped.
    """
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", str(value))


def paragraphs(value: str) -> str:
    """Separate long stored text into short display blocks without dropping words.

    Existing line boundaries remain boundaries. Long words, URLs and identifiers
    stay intact; this display-only transformation never rewrites the database.

    Args:
        value (str): Raw specification, comment or other stored user text.

    Returns:
        str: Escaped Markdown blocks separated by one blank line, about 240 chars
            each; an indivisible longer word may exceed that display target.
    """
    return "\n\n".join(
        markdown(part)
        for line in value.splitlines()
        for part in wrap(
            line, width=BLOCK_WIDTH, break_long_words=False, break_on_hyphens=False
        )
    )
