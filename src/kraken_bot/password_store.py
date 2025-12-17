from __future__ import annotations

import logging
from typing import Optional

import keyring
from keyring.errors import PasswordDeleteError

logger = logging.getLogger(__name__)

_SERVICE = "Krakked"
_ACCOUNT = "master_password"


def get_saved_master_password() -> Optional[str]:
    try:
        return keyring.get_password(_SERVICE, _ACCOUNT)
    except Exception as exc:
        logger.warning("Keyring read failed: %s", exc)
        return None


def save_master_password(password: str) -> None:
    try:
        keyring.set_password(_SERVICE, _ACCOUNT, password)
    except Exception as exc:
        raise RuntimeError("Failed to save master password to OS keyring.") from exc


def delete_master_password() -> None:
    try:
        keyring.delete_password(_SERVICE, _ACCOUNT)
    except PasswordDeleteError:
        return
    except Exception as exc:
        raise RuntimeError("Failed to delete master password from OS keyring.") from exc
