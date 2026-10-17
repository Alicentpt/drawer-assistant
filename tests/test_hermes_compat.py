# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Verify compatibility updates are repeatable and reject conflicting source."""

from typing import TYPE_CHECKING

import pytest

from drawer_assistant import hermes_compat

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize("conflict", [False, True])
def test_patch_application(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, conflict: bool
) -> None:
    """Apply a real Git patch twice, or leave incompatible source untouched.

    Args:
        tmp_path (Path): Disposable source and package directory.
        monkeypatch (pytest.MonkeyPatch): Redirects the bundled patch location.
        conflict (bool): Whether the source intentionally differs from the patch.

    Returns:
        None: Assertions confirm idempotence or refusal without partial writes.

    Raises:
        AssertionError: Applying changes unrelated content or accepts a conflict.
        OSError: Fixture files or Git cannot be accessed.
        RuntimeError: Git is missing, times out or unexpectedly rejects the patch.
    """  # noqa: DOC502 - Filesystem, subprocess and assertion failures propagate.
    monkeypatch.setattr(hermes_compat, "__file__", str(tmp_path / "compat.py"))
    (tmp_path / "telegram-albums.patch").write_text(
        "--- a/adapter.txt\n+++ b/adapter.txt\n@@ -1 +1 @@\n-before\n+after\n",
        encoding="utf-8",
    )
    source = tmp_path / "source"
    source.mkdir()
    adapter = source / "adapter.txt"
    original = "unrelated edit\n" if conflict else "before\n"
    adapter.write_text(original, encoding="utf-8")
    if conflict:
        with pytest.raises(RuntimeError, match="incompatible"):
            hermes_compat.patch_telegram(source)
        assert adapter.read_text("utf-8") == original
    else:
        assert hermes_compat.patch_telegram(source) is True
        assert hermes_compat.patch_telegram(source) is False
        assert adapter.read_text("utf-8") == "after\n"
