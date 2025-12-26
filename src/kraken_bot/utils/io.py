"""I/O and utility functions for file management."""

import logging
import os
import shutil
import stat
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def sanitize_filename(name: str) -> str:
    """
    Sanitizes a string to be safe for use as a filename.

    Removes all characters except alphanumerics, hyphens, and underscores.
    Strips leading and trailing whitespace.

    Args:
        name: The candidate filename string.

    Returns:
        The sanitized filename.

    Raises:
        ValueError: If the sanitized name is empty or consists only of '.' or '..'.
    """
    safe_name = "".join(c for c in name if c.isalnum() or c in ("-", "_")).strip()
    if not safe_name or safe_name == "." or safe_name == "..":
        raise ValueError(f"Invalid filename: {name}")
    return safe_name


def backup_file(path: Path) -> Optional[Path]:
    """
    Creates a timestamped backup of the given file.

    The backup filename format is `<original_name>.<timestamp>.bak`.
    The operation is skipped if the source file does not exist.

    Args:
        path: The path to the file to backup.

    Returns:
        The path to the backup file if successful, or None if the source did not exist.

    Raises:
        OSError: If the copy operation fails (e.g., permission errors),
                 the exception is logged and re-raised.
    """
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
    mode: str = "w",
    dump_func: Optional[Callable[[Any, Any], None]] = None,
) -> None:
    """
    Writes content to a file atomically using a temporary file and rename.

    Ensures that the target file is either fully written or not modified at all.
    This prevents file corruption if the process crashes during writing.

    Preserves the file permissions of the target file if it already exists by
    creating the temporary file with matching permissions.

    Args:
        path: The target file path.
        content: The data to write (string, bytes, or object if dump_func is used).
        mode: File open mode (default 'w'). Use 'wb' for binary data.
        dump_func: Optional callable (e.g., `yaml.safe_dump`, `json.dump`) that
                   accepts `(content, file_handle)` to serialize objects.

    Raises:
        Exception: If writing or renaming fails. The temporary file is cleaned up.
    """
    path = Path(path)
    tmp_path = path.with_suffix(path.suffix + ".tmp")

    # Aegis: prevent information leakage by preserving permissions of sensitive files
    original_mode = None
    if path.exists():
        try:
            # Capture only the permission bits
            original_mode = stat.S_IMODE(path.stat().st_mode)
        except Exception:
            pass  # Best effort

    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        # Use os.open to set permissions at creation time (no race window)
        # Default to 0o666 if new file (respects umask)
        create_mode = original_mode if original_mode is not None else 0o666
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        # Use getattr to avoid pyright errors on non-Windows systems
        flags |= int(getattr(os, "O_BINARY", 0))

        # We must handle binary mode vs text mode for fdopen
        fd = os.open(tmp_path, flags, create_mode)
        try:
            f = os.fdopen(fd, mode)
        except Exception:
            os.close(fd)
            raise

        with f:
            if dump_func:
                dump_func(content, f)
            else:
                f.write(content)

        # Best-effort chmod to ensure exact bits (in case umask stripped something we wanted)
        if original_mode is not None:
            try:
                os.chmod(tmp_path, original_mode)
            except Exception:
                pass

        # Windows compatibility for atomic replace?
        # path.replace(tmp_path) -> replace fails if dst exists on Windows sometimes without unlink
        # But standard lib replace should be atomic on POSIX.
        tmp_path.replace(path)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass
        raise


def deep_merge_dicts(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    """
    Recursively merges the 'overlay' dictionary into the 'base' dictionary.

    If a key exists in both and both values are dictionaries, they are merged recursively.
    Otherwise, the value from 'overlay' overwrites the value in 'base'.
    The original dictionaries are not modified; a new merged dictionary is returned.

    Args:
        base: The base dictionary.
        overlay: The dictionary with updates to apply.

    Returns:
        A new dictionary containing the merged result.
    """
    merged = base.copy()
    for key, value in overlay.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged
