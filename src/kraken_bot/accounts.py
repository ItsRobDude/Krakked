from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import yaml  # type: ignore[import-untyped]

from kraken_bot.config import get_config_dir
from kraken_bot.utils.io import atomic_write, backup_file

ACCOUNTS_FILENAME = "accounts.yaml"


@dataclass
class AccountMeta:
    id: str
    name: str
    region: str
    secrets_path: str  # Relative to config_dir or absolute
    created_at: str  # ISO format
    last_used_at: Optional[str] = None


@dataclass
class AccountRegistry:
    version: int = 1
    accounts: List[AccountMeta] = field(default_factory=list)


def load_accounts(config_dir: Path | None = None) -> Dict[str, AccountMeta]:
    """
    Loads the account registry from disk. Returns a dictionary keyed by account_id.
    Ensures default account structure exists.
    """
    if config_dir is None:
        config_dir = get_config_dir()

    registry_path = config_dir / ACCOUNTS_FILENAME
    registry = AccountRegistry()

    if registry_path.exists():
        try:
            with open(registry_path, "r") as f:
                data = yaml.safe_load(f) or {}

            if isinstance(data, dict):
                # Basic schema validation
                version = data.get("version", 1)
                accounts_data = data.get("accounts", [])

                loaded_accounts = []
                for acc in accounts_data:
                    if isinstance(acc, dict):
                        try:
                            loaded_accounts.append(AccountMeta(**acc))
                        except TypeError:
                            # Skip malformed entries
                            pass

                registry = AccountRegistry(version=version, accounts=loaded_accounts)
        except Exception:
            # If corrupt, we'll return default registry (empty -> ensure_default adds default)
            pass

    # Convert to dict for easier lookup
    account_map = {acc.id: acc for acc in registry.accounts}
    return account_map


def save_accounts(config_dir: Path | None, accounts: Dict[str, AccountMeta]) -> None:
    """
    Persists the account registry to disk using atomic write and backup.
    """
    if config_dir is None:
        config_dir = get_config_dir()

    registry_path = config_dir / ACCOUNTS_FILENAME

    # Convert dict back to list for serialization
    account_list = [asdict(acc) for acc in accounts.values()]
    data = {"version": 1, "accounts": account_list}

    backup_file(registry_path)
    atomic_write(registry_path, data, dump_func=yaml.safe_dump)


def ensure_default_account(config_dir: Path | None = None) -> None:
    """
    Ensures that the registry exists and contains the 'default' account.
    """
    if config_dir is None:
        config_dir = get_config_dir()

    accounts = load_accounts(config_dir)

    if "default" not in accounts:
        default_account = AccountMeta(
            id="default",
            name="Default",
            region="US",
            secrets_path="secrets.enc",
            created_at=datetime.now(timezone.utc).isoformat(),
            last_used_at=None,
        )
        accounts["default"] = default_account
        save_accounts(config_dir, accounts)


def resolve_secrets_path(config_dir: Path | None, account_id: str) -> Path:
    """
    Resolves the secrets file path for a given account ID.
    Safety: Internally calls ensure_default_account to guarantee registry existence.
    """
    if config_dir is None:
        config_dir = get_config_dir()

    # Safety guarantee: Ensure registry exists before resolution
    ensure_default_account(config_dir)

    accounts = load_accounts(config_dir)
    account = accounts.get(account_id)

    if not account:
        # Fallback to default if account not found (should be handled by caller, but safe default)
        # Or raise error? raising error is better for debugging logic flaws.
        # But per requirements, let's assume if it's missing we might want default or fail.
        # Let's fail if not found, but we ensure default exists.
        if account_id == "default":
            # Should be there due to ensure_default_account, but defensive coding
            return config_dir / "secrets.enc"
        raise ValueError(f"Account {account_id} not found in registry")

    path = Path(account.secrets_path)
    if not path.is_absolute():
        path = config_dir / path

    # Aegis: Unrestricted file path resolution -> bounded to config directory (no exploit details)
    if not path.resolve().is_relative_to(config_dir.resolve()):
        raise ValueError(
            f"Account {account_id} secrets path is outside the config directory"
        )

    return path
