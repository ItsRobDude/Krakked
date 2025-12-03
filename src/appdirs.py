"""Lightweight appdirs fallback used when the external dependency isn't installed.

This implements the small subset of functions the project relies on so tests can
run in minimal environments without pulling the upstream package.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Optional


def _default_base_dir(env_var: str, fallback: Path) -> Path:
    override = os.environ.get(env_var)
    return Path(override) if override else fallback


def user_config_dir(appname: str, appauthor: Optional[str] | None = None) -> str:
    """Return an OS-appropriate user config directory for the given appname."""

    system = platform.system()
    home = Path.home()

    if system == "Windows":
        base = _default_base_dir("APPDATA", home)
    elif system == "Darwin":
        base = home / "Library" / "Application Support"
    else:
        base = _default_base_dir("XDG_CONFIG_HOME", home / ".config")

    return str(base / appname)


def user_data_dir(appname: str, appauthor: Optional[str] | None = None) -> str:
    """Return an OS-appropriate user data directory for the given appname."""

    system = platform.system()
    home = Path.home()

    if system == "Windows":
        base = _default_base_dir("APPDATA", home)
    elif system == "Darwin":
        base = home / "Library" / "Application Support"
    else:
        base = _default_base_dir("XDG_DATA_HOME", home / ".local" / "share")

    return str(base / appname)


__all__ = ["user_config_dir", "user_data_dir"]
