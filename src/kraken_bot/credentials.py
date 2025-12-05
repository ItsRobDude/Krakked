from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class CredentialStatus(Enum):
    LOADED = "loaded"
    NOT_FOUND = "not_found"
    MISSING_PASSWORD = "missing_password"
    LOCKED = "locked"
    AUTH_ERROR = "auth_error"
    SERVICE_ERROR = "service_error"
    DECRYPTION_FAILED = "decryption_failed"


@dataclass
class CredentialResult:
    api_key: Optional[str]
    api_secret: Optional[str]
    status: CredentialStatus
    source: Optional[str] = None
    validated: Optional[bool] = None
    can_force_save: bool = False
    validation_error: Optional[str] = None
    error: Optional[Exception] = None

    def __repr__(self) -> str:
        # Intentionally omit secrets to avoid leaking them via logs.
        return (
            "CredentialResult("
            f"status={self.status!r}, "
            f"source={self.source!r}, "
            f"validated={self.validated!r}, "
            f"can_force_save={self.can_force_save!r}, "
            f"validation_error={self.validation_error!r}, "
            f"error={type(self.error).__name__ if self.error else None})"
        )

    __str__ = __repr__
