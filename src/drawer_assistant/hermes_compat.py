# Copyright (c) 2026 Drawer Assistant contributors.
# SPDX-License-Identifier: GPL-3.0-only
"""Apply the narrow, opt-in Telegram album fix to the installed Hermes adapter."""

import shutil

# Fixed git apply arguments; no shell or user-supplied command is executed.
import subprocess  # nosec B404
from pathlib import Path


def patch_telegram(source: Path) -> bool:
    """Apply the bundled patch once, refusing incompatible upstream adapter code.

    Args:
        source (Path): Hermes source checkout containing plugins/platforms/telegram.

    Returns:
        bool: True if applied now, False if this exact patch is already installed.

    Raises:
        OSError: Git, patch or source files cannot be accessed.
        RuntimeError: Git is missing, times out, or rejects the incompatible patch.
    """  # noqa: DOC503 - File and subprocess errors propagate.
    git = shutil.which("git")
    if git is None:
        message = "Git is required to install the Hermes album compatibility patch."
        raise RuntimeError(message)
    patch = Path(__file__).with_name("telegram-albums.patch")
    for arguments in (("--reverse", "--check"), ("--check",), ()):
        # Executable resolved from PATH; only fixed apply flags and a packaged patch.
        try:
            result = subprocess.run(  # noqa: S603  # nosec B603
                [git, "apply", "--ignore-space-change", *arguments, "--", str(patch)],
                cwd=source,
                capture_output=True,
                check=False,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            message = "Checking or applying the Hermes album patch timed out."
            raise RuntimeError(message) from None
        if arguments == ("--reverse", "--check"):
            if result.returncode == 0:
                return False
        elif result.returncode:
            message = (
                "Hermes album patch is incompatible; source was not silently replaced."
            )
            raise RuntimeError(message)
    return True
