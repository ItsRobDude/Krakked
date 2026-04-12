import os
import stat

import pytest

from krakked.utils.io import atomic_write


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX permissions tests not relevant on Windows"
)
def test_atomic_write_preserves_strict_permissions(tmp_path):
    """
    Verify that atomic_write preserves 0600 permissions on update.
    """
    target = tmp_path / "sensitive.txt"
    target.write_text("initial")

    # Set strict permissions: read/write for owner only
    os.chmod(target, 0o600)

    # Verify setup
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    # Update content
    atomic_write(target, "updated")

    # Verify permissions preserved
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert target.read_text() == "updated"

    # Verify no temp file residue
    assert not (target.with_suffix(target.suffix + ".tmp")).exists()


@pytest.mark.skipif(
    os.name == "nt", reason="POSIX permissions tests not relevant on Windows"
)
def test_atomic_write_preserves_group_permissions(tmp_path):
    """
    Verify that atomic_write preserves 0640 permissions (owner read/write, group read).
    """
    target = tmp_path / "group_config.yaml"
    target.write_text("initial")

    # Set group readable
    os.chmod(target, 0o640)

    # Update content
    atomic_write(target, "updated")

    # Verify permissions
    assert stat.S_IMODE(target.stat().st_mode) == 0o640


def test_atomic_write_new_file_defaults(tmp_path):
    """
    Verify behavior for new files (should respect umask/defaults).
    """
    target = tmp_path / "new_file.txt"
    atomic_write(target, "content")

    assert target.exists()
    # We don't assert specific mode here as it depends on system umask,
    # but we verify the write succeeded.
    assert target.read_text() == "content"
