from __future__ import annotations

import logging
from typing import Optional

import keyring
from keyring.errors import PasswordDeleteError

logger = logging.getLogger(__name__)

_SERVICE = "Krakked"
_OLD_ACCOUNT_KEY = "master_password"


def get_saved_master_password(account_id: str) -> Optional[str]:
    """
    Retrieves the master password from the OS keyring for a specific account.
    Supports legacy migration for the 'default' account.
    """
    new_key = f"master_password:{account_id}"
    try:
        # 1. Try new per-account key
        password = keyring.get_password(_SERVICE, new_key)
        if password:
            return password

        # 2. Migration: If account is default and new key missing, check legacy key
        if account_id == "default":
            legacy_password = keyring.get_password(_SERVICE, _OLD_ACCOUNT_KEY)
            if legacy_password:
                # Migrate immediately
                try:
                    keyring.set_password(_SERVICE, new_key, legacy_password)
                    logger.info(
                        "Migrated legacy master password to account-specific key"
                    )
                except Exception as exc:
                    logger.warning("Failed to migrate legacy password: %s", exc)
                return legacy_password

        return None
    except Exception as exc:
        logger.warning("Keyring read failed for %s: %s", account_id, exc)
        return None


def save_master_password(account_id: str, password: str) -> None:
    """Saves the master password for a specific account."""
    new_key = f"master_password:{account_id}"
    try:
        keyring.set_password(_SERVICE, new_key, password)
    except Exception as exc:
        raise RuntimeError(
            f"Failed to save master password for {account_id} to OS keyring."
        ) from exc


def delete_master_password(account_id: str) -> None:
    """Deletes the master password for a specific account. Handles legacy key cleanup for default."""
    new_key = f"master_password:{account_id}"

    # Best effort deletion
    try:
        keyring.delete_password(_SERVICE, new_key)
    except PasswordDeleteError:
        pass
    except Exception as exc:
        logger.warning("Failed to delete password for %s: %s", account_id, exc)

    # Cleanup legacy key if deleting default account
    if account_id == "default":
        try:
            keyring.delete_password(_SERVICE, _OLD_ACCOUNT_KEY)
        except PasswordDeleteError:
            pass
        except Exception as exc:
            logger.warning("Failed to delete legacy password key: %s", exc)
