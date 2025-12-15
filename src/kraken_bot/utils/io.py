"""I/O and utility functions for file management."""

import logging
import time
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Callable

import yaml

logger = logging.getLogger(__name__)

def sanitize_filename(name: str) -> str:
    """
    Sanitizes a string to be safe for use as a filename.
    Allows alphanumeric, hyphens, and underscores.
    Rejects empty strings or dot-only strings.
    """
    safe_name = "".join(c for c in name if c.isalnum() or c in ('-', '_')).strip()
    if not safe_name or safe_name == "." or safe_name == "..":
        raise ValueError(f"Invalid filename: {name}")
    return safe_name

def backup_file(path: Path) -> Optional[Path]:
    """Creates a timestamped backup of the given file."""
    if not path.exists():
        return None
    timestamp = int(time.time())
    backup_path = path.with_name(f"{path.name}.{timestamp}.bak")
    try:
        shutil.copy2(path, backup_path)
        return backup_path
    except Exception as e:
        logger.error(f"Failed to backup {path}: {e}")
        raise

def atomic_write(
    path: Path,
    content: Any,
    mode: str = 'w',
    dump_func: Optional[Callable[[Any, Any], None]] = None
) -> None:
    """
    Writes content to a file atomically using a temporary file and rename.
    Supports simple write (string/bytes) or a dump function (e.g. yaml.safe_dump).
    """
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp_path, mode) as f:
            if dump_func:
                dump_func(content, f)
            else:
                f.write(content)

        # Windows compatibility for atomic replace?
        # path.replace(tmp_path) -> replace fails if dst exists on Windows sometimes without unlink
        # But standard lib replace should be atomic on POSIX.
        tmp_path.replace(path)
    except Exception as e:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise e

def deep_merge_dicts(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merges overlay into base.
    """
    merged = base.copy()
    for key, value in overlay.items():
        if (
            key in merged
            and isinstance(merged[key], dict)
            and isinstance(value, dict)
        ):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged
