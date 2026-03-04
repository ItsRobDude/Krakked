"""System and health endpoints."""

from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional

import yaml  # type: ignore[import-untyped]
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

import kraken_bot.connection.validation as validation_mod
from kraken_bot import APP_VERSION
from kraken_bot.accounts import (
    AccountMeta,
    ensure_default_account,
    load_accounts,
    resolve_secrets_path,
    save_accounts,
)
from kraken_bot.config import dump_runtime_overrides, get_config_dir
from kraken_bot.config_loader import (
    _load_yaml_mapping,
    _resolve_effective_env,
    parse_app_config,
    write_initial_config,
)
from kraken_bot.connection.exceptions import (
    AuthError,
    KrakenAPIError,
    ServiceUnavailableError,
)
from kraken_bot.credentials import CredentialStatus
from kraken_bot.market_data.api import MarketDataStatus
from kraken_bot.password_store import (
    delete_master_password,
    get_saved_master_password,
    save_master_password,
)
from kraken_bot.secrets import (
    SecretsDecryptionError,
    delete_secrets,
    persist_api_keys,
    set_session_master_password,
    unlock_secrets,
)
from kraken_bot.ui.logging import build_request_log_extra
from kraken_bot.ui.models import ApiEnvelope, SystemHealthPayload, SystemMetricsPayload
from kraken_bot.utils.io import (
    atomic_write,
    backup_file,
    deep_merge_dicts,
    sanitize_filename,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class CredentialPayload(BaseModel):
    """Payload expected from the UI when validating credentials."""

    apiKey: str
    apiSecret: str
    region: str


class SetupCredentialsPayload(BaseModel):
    """Payload for saving credentials during setup."""

    apiKey: str
    apiSecret: str
    password: str
    region: Optional[str] = "US"


class SetupUnlockPayload(BaseModel):
    """Payload for unlocking secrets."""

    password: str
    remember: bool = False


class SetupConfigPayload(BaseModel):
    """Payload for creating initial configuration."""

    region_code: str
    universe_pairs: list[str] = Field(default_factory=list)


class SetupStatusPayload(BaseModel):
    """Status of the setup/onboarding process."""

    configured: bool
    secrets_exist: bool
    unlocked: bool


class ModeChangePayload(BaseModel):
    """Payload for toggling the execution mode."""

    mode: Literal["paper", "live"]
    # Optional guard fields for live mode transition
    password: Optional[str] = None
    confirmation: Optional[str] = None


class SessionConfigPayload(BaseModel):
    """Payload for starting or updating a trading session."""

    profile_name: str
    mode: Literal["paper", "live"]
    loop_interval_sec: float = Field(15.0, ge=1.0, le=300.0)


class SessionConfigPatchPayload(BaseModel):
    """Payload for updating session configuration while stopped."""

    profile_name: Optional[str] = None
    mode: Optional[Literal["paper", "live"]] = None
    loop_interval_sec: Optional[float] = Field(None, ge=1.0, le=300.0)


class SessionStatePayload(BaseModel):
    """Response payload describing the current session state."""

    active: bool
    mode: str
    loop_interval_sec: float
    profile_name: Optional[str]
    ml_enabled: bool
    emergency_flatten: bool = False
    account_id: str


class ProfileSummaryPayload(BaseModel):
    """Simplified profile metadata for UI selection."""

    name: str
    description: str


class ProfileCreatePayload(BaseModel):
    """Payload for creating a new profile."""

    name: str
    description: str = ""
    default_mode: str = "paper"
    base_config: Optional[dict] = None


# --- Account Payloads ---


class AccountItemPayload(BaseModel):
    id: str
    name: str
    region: str
    secrets_exist: bool
    remembered: bool


class AccountListPayload(BaseModel):
    accounts: List[AccountItemPayload]
    selected_account_id: str


class AccountSelectPayload(BaseModel):
    account_id: str


class AccountCreatePayload(BaseModel):
    name: str
    apiKey: str
    apiSecret: str
    password: str
    region: str = "US"
    remember: bool = False


class AccountUnlockPayload(BaseModel):
    password: Optional[str] = None
    use_saved_password: bool = False
    remember: bool = False


def _context(request: Request):
    return request.app.state.context


def _redacted_config(config) -> dict:
    config_dict = asdict(config)
    ui_config = config_dict.get("ui", {})
    auth_config = ui_config.get("auth")
    if isinstance(auth_config, dict) and "token" in auth_config:
        auth_config["token"] = "***"
    return config_dict


def _session_payload(ctx) -> SessionStatePayload:
    session = ctx.session
    return SessionStatePayload(
        active=session.active,
        mode=session.mode,
        loop_interval_sec=session.loop_interval_sec,
        profile_name=session.profile_name,
        ml_enabled=session.ml_enabled,
        emergency_flatten=getattr(session, "emergency_flatten", False),
        account_id=session.account_id,
    )


def _persist_session_config_to_main_config(
    config_dir: Path,
    *,
    profile_name: Optional[str] = None,
    mode: Optional[str] = None,
    loop_interval_sec: Optional[float] = None,
) -> None:
    """Persists session configuration to the main config file, excluding active state."""
    main_config_path = config_dir / "config.yaml"

    try:
        if main_config_path.exists():
            with open(main_config_path, "r") as f:
                main_data = yaml.safe_load(f) or {}
        else:
            main_data = {}

        if not isinstance(main_data.get("session"), dict):
            main_data["session"] = {}

        session_data = main_data["session"]

        if profile_name is not None:
            session_data["profile_name"] = profile_name
        if mode is not None:
            session_data["mode"] = mode
        if loop_interval_sec is not None:
            session_data["loop_interval_sec"] = loop_interval_sec

        # Explicitly remove legacy ml_enabled field from session if present
        session_data.pop("ml_enabled", None)

        # Never persist active state to disk
        session_data.pop("active", None)

        backup_file(main_config_path)
        atomic_write(main_config_path, main_data, dump_func=yaml.safe_dump)
    except Exception as e:
        logger.error(f"Failed to persist session config to main config: {e}")


def _check_setup_mode(ctx):
    """Raises 503 if the system is in setup mode."""
    if ctx.is_setup_mode:
        raise HTTPException(
            status_code=503,
            detail="System is in setup mode. Please complete configuration.",
        )


@router.get("/setup/status", response_model=ApiEnvelope[SetupStatusPayload])
async def setup_status(request: Request) -> ApiEnvelope[SetupStatusPayload]:
    """Returns the current setup status (config present? secrets present? unlocked?)."""
    ctx = _context(request)
    config_dir = get_config_dir()
    config_path = config_dir / "config.yaml"

    # Resolve secrets path for current account
    account_id = ctx.session.account_id
    try:
        secrets_path = resolve_secrets_path(config_dir, account_id)
        secrets_exist = secrets_path.exists()
    except Exception:
        secrets_exist = False

    configured = config_path.exists()

    # If secrets exist but we are still in setup mode, it means they are locked
    # (or config is missing, but 'configured' flag covers that).
    # If ctx.is_setup_mode is False, we are unlocked.
    unlocked = not ctx.is_setup_mode if secrets_exist else False

    return ApiEnvelope(
        data=SetupStatusPayload(
            configured=configured, secrets_exist=secrets_exist, unlocked=unlocked
        ),
        error=None,
    )


@router.post("/setup/config", response_model=ApiEnvelope[dict])
async def setup_config(
    payload: SetupConfigPayload, request: Request
) -> ApiEnvelope[dict]:
    """Writes the initial configuration file."""
    try:
        config_data = {
            "region": {"code": payload.region_code, "default_quote": "USD"},
            "universe": {"include_pairs": payload.universe_pairs},
            # Default minimal structure
            "execution": {"mode": "paper"},
            "ui": {"enabled": True, "port": 8000},
            # Initialize with default session account
            "session": {"account_id": "default"},
            # Default ML config
            "ml": {"enabled": True},
        }
        write_initial_config(config_data)

        # Also ensure default account exists in registry
        ensure_default_account()

        logger.info(
            "Initial configuration written",
            extra=build_request_log_extra(request, event="setup_config_written"),
        )
        return ApiEnvelope(data={"success": True}, error=None)
    except Exception as exc:
        logger.exception(
            "Failed to write configuration",
            extra=build_request_log_extra(request, event="setup_config_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/setup/credentials", response_model=ApiEnvelope[dict])
async def setup_credentials(
    payload: SetupCredentialsPayload, request: Request
) -> ApiEnvelope[dict]:
    """Validates and saves encrypted credentials."""
    ctx = _context(request)
    account_id = ctx.session.account_id

    try:
        # 1. Validate against Kraken
        result = validation_mod.validate_credentials(
            payload.apiKey, payload.apiSecret, region=payload.region
        )

        if not result.validated:
            return ApiEnvelope(
                data={"valid": False},
                error=f"Validation failed: {result.validation_error or result.error}",
            )

        # 2. Persist
        secrets_path = resolve_secrets_path(None, account_id)
        persist_api_keys(
            api_key=payload.apiKey,
            api_secret=payload.apiSecret,
            password=payload.password,
            validated=True,
            secrets_path=secrets_path,
        )

        logger.info(
            "Credentials saved via setup",
            extra=build_request_log_extra(request, event="setup_credentials_saved"),
        )
        return ApiEnvelope(data={"success": True}, error=None)

    except Exception as exc:
        logger.exception(
            "Failed to save credentials",
            extra=build_request_log_extra(request, event="setup_credentials_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/setup/unlock", response_model=ApiEnvelope[dict])
async def setup_unlock(
    payload: SetupUnlockPayload, request: Request
) -> ApiEnvelope[dict]:
    """Attempts to unlock the system with the master password."""
    ctx = _context(request)
    account_id = ctx.session.account_id

    try:
        secrets_path = resolve_secrets_path(None, account_id)
        # Verify password by attempting decryption
        _ = unlock_secrets(payload.password, secrets_path=secrets_path)

        # Set session password for re-bootstrap
        set_session_master_password(account_id, payload.password)

        remember_saved = False
        remember_error: str | None = None

        if payload.remember:
            try:
                save_master_password(account_id, payload.password)
                remember_saved = True
            except Exception as exc:
                remember_error = str(exc)
                logger.warning(
                    "Remember-me save failed (ignoring): %s",
                    exc,
                    extra=build_request_log_extra(
                        request,
                        event="setup_unlock_remember_failed",
                        error=remember_error,
                    ),
                )

        if ctx.is_setup_mode:
            logger.info(
                "Unlock successful, signaling re-initialization",
                extra=build_request_log_extra(request, event="setup_unlock_success"),
            )
            ctx.reinitialize_event.set()

        return ApiEnvelope(
            data={
                "success": True,
                "remember_saved": remember_saved,
                "remember_error": remember_error,
            },
            error=None,
        )

    except SecretsDecryptionError:
        logger.warning(
            "Unlock failed: invalid password",
            extra=build_request_log_extra(request, event="setup_unlock_failed"),
        )
        return ApiEnvelope(data=None, error="Invalid password")
    except Exception as exc:
        logger.exception(
            "Unlock failed with error",
            extra=build_request_log_extra(request, event="setup_unlock_error"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/setup/forget", response_model=ApiEnvelope[dict])
async def system_forget(request: Request) -> ApiEnvelope[dict]:
    """Forgets the master password from this device for the selected account."""
    ctx = _context(request)
    account_id = ctx.session.account_id
    try:
        set_session_master_password(account_id, None)
        delete_master_password(account_id)

        # Clean up env var if it exists from legacy flow
        import os

        os.environ.pop("KRAKEN_BOT_SECRET_PW", None)

        logger.info(
            f"Master password forgotten for account {account_id}",
            extra=build_request_log_extra(
                request, event="system_forget", account_id=account_id
            ),
        )
        return ApiEnvelope(data={"success": True}, error=None)
    except Exception as exc:
        logger.exception(
            "Failed to forget master password",
            extra=build_request_log_extra(request, event="system_forget_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/reset", response_model=ApiEnvelope[dict])
async def system_reset(request: Request) -> ApiEnvelope[dict]:
    """Resets the system by deleting credentials and entering setup mode."""
    ctx = _context(request)
    account_id = ctx.session.account_id
    try:
        # Resolve path to delete correct secrets file
        try:
            secrets_path = resolve_secrets_path(None, account_id)
        except Exception as exc:
            logger.error(f"Failed to resolve secrets path during reset: {exc}")
            return ApiEnvelope(
                data=None, error="Failed to resolve secrets path for selected account"
            )

        delete_secrets(secrets_path)

        # Also forget the password since the file it unlocks is gone
        set_session_master_password(account_id, None)

        try:
            delete_master_password(account_id)
        except Exception as exc:
            logger.warning(
                "Failed to delete master password from keyring during reset (ignoring)",
                extra=build_request_log_extra(
                    request, event="reset_keyring_error", error=str(exc)
                ),
            )

        ctx.is_setup_mode = True

        logger.info(
            f"System reset requested for account {account_id}: credentials deleted",
            extra=build_request_log_extra(
                request, event="system_reset", account_id=account_id
            ),
        )
        return ApiEnvelope(data={"success": True}, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "System reset failed",
            extra=build_request_log_extra(request, event="system_reset_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


# --- Accounts API ---


@router.get("/accounts/list", response_model=ApiEnvelope[AccountListPayload])
async def list_accounts(request: Request) -> ApiEnvelope[AccountListPayload]:
    ctx = _context(request)
    # Do NOT check setup mode - allowed in locked state

    config_dir = get_config_dir()
    ensure_default_account(config_dir)
    accounts_map = load_accounts(config_dir)

    # Validation: Force valid selection
    selected_id = ctx.session.account_id
    if selected_id not in accounts_map:
        logger.warning(
            f"Selected account {selected_id} not found in registry, resetting to default"
        )
        selected_id = "default"
        ctx.session.account_id = "default"
        ctx.config.session.account_id = "default"
        dump_runtime_overrides(ctx.config, session=ctx.session, sections={"session"})

    payload_list = []
    for acc in accounts_map.values():
        secrets_path = resolve_secrets_path(config_dir, acc.id)

        # Check if remembered
        remembered = bool(get_saved_master_password(acc.id))

        payload_list.append(
            AccountItemPayload(
                id=acc.id,
                name=acc.name,
                region=acc.region,
                secrets_exist=secrets_path.exists(),
                remembered=remembered,
            )
        )

    return ApiEnvelope(
        data=AccountListPayload(accounts=payload_list, selected_account_id=selected_id),
        error=None,
    )


@router.post("/accounts/select", response_model=ApiEnvelope[dict])
async def select_account(
    payload: AccountSelectPayload, request: Request
) -> ApiEnvelope[dict]:
    ctx = _context(request)
    # Do NOT check setup mode

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot switch accounts while session is active"
        )

    config_dir = get_config_dir()
    accounts_map = load_accounts(config_dir)

    if payload.account_id not in accounts_map:
        return ApiEnvelope(data=None, error=f"Account {payload.account_id} not found")

    # Update last used
    acc = accounts_map[payload.account_id]
    acc.last_used_at = datetime.now(timezone.utc).isoformat()
    save_accounts(config_dir, accounts_map)

    # Capture old ID before update
    old_account_id = ctx.session.account_id

    # Update Session
    ctx.session.account_id = payload.account_id
    ctx.config.session.account_id = payload.account_id
    dump_runtime_overrides(ctx.config, session=ctx.session, sections={"session"})

    # Force Setup Mode (Locked)
    ctx.is_setup_mode = True

    # Clear session passwords for safety (both old and new)
    set_session_master_password(old_account_id, None)
    set_session_master_password(payload.account_id, None)

    logger.info(
        f"Switched from {old_account_id} to {payload.account_id}",
        extra=build_request_log_extra(
            request, event="account_switched", account_id=payload.account_id
        ),
    )

    return ApiEnvelope(data={"success": True}, error=None)


@router.post("/accounts/create", response_model=ApiEnvelope[dict])
async def create_account(
    payload: AccountCreatePayload, request: Request
) -> ApiEnvelope[dict]:
    ctx = _context(request)
    # Do NOT check setup mode

    # 1. Validate Credentials
    try:
        result = validation_mod.validate_credentials(
            payload.apiKey, payload.apiSecret, region=payload.region
        )
        if not result.validated:
            return ApiEnvelope(
                data=None,
                error=f"Validation failed: {result.validation_error or result.error}",
            )
    except Exception as e:
        return ApiEnvelope(data=None, error=f"Validation error: {e}")

    # 2. Generate ID
    base_slug = sanitize_filename(payload.name).lower()
    if not base_slug:
        base_slug = "account"

    config_dir = get_config_dir()
    ensure_default_account(config_dir)
    accounts_map = load_accounts(config_dir)

    account_id = base_slug
    counter = 2
    while account_id in accounts_map:
        account_id = f"{base_slug}-{counter}"
        counter += 1

    # 3. Resolve Paths & Persist
    secrets_rel_path = f"accounts/{account_id}/secrets.enc"
    secrets_path = config_dir / secrets_rel_path

    # Ensure directory exists
    secrets_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        persist_api_keys(
            api_key=payload.apiKey,
            api_secret=payload.apiSecret,
            password=payload.password,
            validated=True,
            secrets_path=secrets_path,
        )
    except Exception as e:
        return ApiEnvelope(data=None, error=f"Failed to save secrets: {e}")

    # 4. Update Registry
    new_account = AccountMeta(
        id=account_id,
        name=payload.name,
        region=payload.region,
        secrets_path=secrets_rel_path,
        created_at=datetime.now(timezone.utc).isoformat(),
        last_used_at=datetime.now(timezone.utc).isoformat(),
    )
    accounts_map[account_id] = new_account
    save_accounts(config_dir, accounts_map)

    # 5. Handle Remember Me
    if payload.remember:
        try:
            save_master_password(account_id, payload.password)
        except Exception as e:
            logger.warning(f"Failed to save password for new account: {e}")

    # 6. Switch & Unlock
    old_account_id = ctx.session.account_id
    ctx.session.account_id = account_id
    ctx.config.session.account_id = account_id
    dump_runtime_overrides(ctx.config, session=ctx.session, sections={"session"})

    # Clear old session password for safety, set new one
    set_session_master_password(old_account_id, None)
    set_session_master_password(account_id, payload.password)

    # Trigger reinit to load new state
    ctx.reinitialize_event.set()

    logger.info(
        f"Created account {account_id}",
        extra=build_request_log_extra(
            request, event="account_created", account_id=account_id
        ),
    )

    return ApiEnvelope(data={"success": True, "account_id": account_id}, error=None)


@router.post("/accounts/unlock", response_model=ApiEnvelope[dict])
async def unlock_account(
    payload: AccountUnlockPayload, request: Request
) -> ApiEnvelope[dict]:
    ctx = _context(request)
    # Do NOT check setup mode

    if ctx.session.active:
        return ApiEnvelope(data=None, error="Cannot unlock while session is active")

    account_id = ctx.session.account_id

    password = payload.password

    if payload.use_saved_password:
        # Loopback check
        # Using config host is reliable for checking configured intention
        ui_host = ctx.config.ui.host
        is_loopback = ui_host in ("127.0.0.1", "::1", "localhost")

        if not is_loopback:
            return ApiEnvelope(
                data=None,
                error="Saved password unlock only allowed on loopback interface",
            )

        saved_pw = get_saved_master_password(account_id)
        if not saved_pw:
            return ApiEnvelope(data=None, error="No saved password for account")
        password = saved_pw

    if not password:
        return ApiEnvelope(data=None, error="Password required")

    try:
        secrets_path = resolve_secrets_path(None, account_id)
        _ = unlock_secrets(password, secrets_path=secrets_path)

        set_session_master_password(account_id, password)

        if (
            payload.remember and payload.password
        ):  # Only update remember if explicit password provided
            try:
                save_master_password(account_id, password)
            except Exception as e:
                logger.warning(f"Failed to remember password: {e}")

        # Update last used
        config_dir = get_config_dir()
        accounts_map = load_accounts(config_dir)
        if account_id in accounts_map:
            accounts_map[account_id].last_used_at = datetime.now(
                timezone.utc
            ).isoformat()
            save_accounts(config_dir, accounts_map)

        # Trigger reinit
        ctx.reinitialize_event.set()

        return ApiEnvelope(data={"success": True}, error=None)

    except SecretsDecryptionError:
        return ApiEnvelope(data=None, error="Invalid password")
    except Exception as e:
        return ApiEnvelope(data=None, error=str(e))


@router.delete("/accounts/{account_id}", response_model=ApiEnvelope[dict])
async def delete_account(account_id: str, request: Request) -> ApiEnvelope[dict]:
    ctx = _context(request)
    # Do NOT check setup mode

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot delete account while session is active"
        )

    if account_id == "default":
        return ApiEnvelope(data=None, error="Cannot delete default account")

    config_dir = get_config_dir()
    accounts_map = load_accounts(config_dir)

    if account_id not in accounts_map:
        return ApiEnvelope(data=None, error="Account not found")

    # 1. Delete Secrets
    try:
        secrets_path = resolve_secrets_path(config_dir, account_id)
        delete_secrets(secrets_path)
        # Try to remove the directory if empty/exists
        if secrets_path.parent.name == account_id:  # Confirm it's the dedicated dir
            try:
                secrets_path.parent.rmdir()
            except Exception:
                pass  # Directory might not be empty or other error
    except Exception as e:
        logger.warning(f"Error cleaning up secrets file: {e}")

    # 2. Delete Keyring
    delete_master_password(account_id)
    set_session_master_password(account_id, None)

    # 3. Remove from Registry
    del accounts_map[account_id]
    save_accounts(config_dir, accounts_map)

    # 4. Handle Selection Reset
    was_selected = ctx.session.account_id == account_id
    if was_selected:
        ctx.session.account_id = "default"
        ctx.config.session.account_id = "default"
        dump_runtime_overrides(ctx.config, session=ctx.session, sections={"session"})
        ctx.is_setup_mode = True
        set_session_master_password("default", None)

    logger.info(
        f"Deleted account {account_id}",
        extra=build_request_log_extra(
            request, event="account_deleted", account_id=account_id
        ),
    )

    return ApiEnvelope(data={"success": True}, error=None)


@router.get("/health", response_model=ApiEnvelope[SystemHealthPayload])
async def system_health(request: Request) -> ApiEnvelope[SystemHealthPayload]:
    try:
        ctx = _context(request)
        if ctx.is_setup_mode:
            # Return a limited health payload in setup mode
            return ApiEnvelope(
                data=SystemHealthPayload(
                    app_version=APP_VERSION,
                    execution_mode="setup",
                    rest_api_reachable=False,
                    websocket_connected=False,
                    streaming_pairs=0,
                    stale_pairs=0,
                    subscription_errors=0,
                    market_data_ok=False,
                    market_data_status="unavailable",
                    market_data_reason="setup_required",
                    market_data_stale=False,
                    execution_ok=False,
                    current_mode="setup",
                    ui_read_only=False,
                    kill_switch_active=False,
                    drift_detected=False,
                    market_data_max_staleness=None,
                ),
                error=None,
            )

        data_status = ctx.market_data.get_data_status()
        metrics_snapshot = ctx.metrics.snapshot()
        execution_config = ctx.config.execution
        market_data_health = ctx.market_data.get_health_status()
        if not isinstance(market_data_health, MarketDataStatus):
            market_data_health = None

        market_data_ok = None
        market_data_stale = None
        market_data_reason = None
        market_data_max_staleness = None

        if market_data_health:
            market_data_ok = getattr(market_data_health, "health", "") == "healthy"
            market_data_stale = getattr(market_data_health, "health", "") == "stale"
            market_data_reason = getattr(market_data_health, "reason", None)
            market_data_max_staleness = getattr(
                market_data_health, "max_staleness", None
            )

        if market_data_ok is None:
            market_data_ok = (
                data_status.rest_api_reachable
                and data_status.websocket_connected
                and data_status.subscription_errors == 0
                and data_status.stale_pairs == 0
            )
        if market_data_stale is None:
            market_data_stale = data_status.stale_pairs > 0
        if market_data_reason is None:
            market_data_reason = (
                None
                if market_data_ok
                else ("data_stale" if market_data_stale else "connection_issue")
            )

        metrics_has_update = bool(metrics_snapshot.get("market_data_status_updated"))

        if metrics_has_update:
            market_data_ok = bool(
                metrics_snapshot.get("market_data_ok", market_data_ok)
            )
            market_data_stale = bool(
                metrics_snapshot.get("market_data_stale", market_data_stale)
            )
            market_data_reason = metrics_snapshot.get(
                "market_data_reason", market_data_reason
            )
            market_data_max_staleness = metrics_snapshot.get(
                "market_data_max_staleness", market_data_max_staleness
            )

        market_data_status = "healthy"
        if not market_data_ok:
            market_data_status = "stale" if market_data_stale else "unavailable"

        execution_ok = execution_config.mode != "live" or bool(
            getattr(execution_config, "allow_live_trading", False)
        )
        risk_status = ctx.strategy_engine.get_risk_status()
        health_payload = SystemHealthPayload(
            app_version=APP_VERSION,
            execution_mode=getattr(execution_config, "mode", None),
            rest_api_reachable=data_status.rest_api_reachable,
            websocket_connected=data_status.websocket_connected,
            streaming_pairs=data_status.streaming_pairs,
            stale_pairs=data_status.stale_pairs,
            subscription_errors=data_status.subscription_errors,
            market_data_ok=bool(market_data_ok),
            market_data_status=market_data_status,
            market_data_reason=market_data_reason,
            market_data_stale=market_data_stale,
            market_data_max_staleness=market_data_max_staleness,
            execution_ok=execution_ok,
            current_mode=execution_config.mode,
            ui_read_only=ctx.config.ui.read_only,
            kill_switch_active=getattr(risk_status, "kill_switch_active", None),
            drift_detected=bool(metrics_snapshot.get("drift_detected")),
            drift_reason=metrics_snapshot.get("drift_reason"),
        )
        return ApiEnvelope(data=health_payload, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch system health",
            extra=build_request_log_extra(request, event="system_health_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/session", response_model=ApiEnvelope[SessionStatePayload])
async def get_session_state(request: Request) -> ApiEnvelope[SessionStatePayload]:
    try:
        ctx = _context(request)
        return ApiEnvelope(data=_session_payload(ctx), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch session state",
            extra=build_request_log_extra(request, event="session_state_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.patch("/session/config", response_model=ApiEnvelope[SessionStatePayload])
async def update_session_config(
    payload: SessionConfigPatchPayload, request: Request
) -> ApiEnvelope[SessionStatePayload]:
    ctx = _context(request)
    _check_setup_mode(ctx)

    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None,
            error="Cannot update session config while session is active. Stop the session first.",
        )

    # Compute next values
    next_profile = (
        payload.profile_name
        if payload.profile_name is not None
        else getattr(ctx.session, "profile_name", None)
    ) or "default"
    next_mode = (
        payload.mode
        if payload.mode is not None
        else getattr(ctx.session, "mode", "paper")
    )
    next_loop = (
        payload.loop_interval_sec
        if payload.loop_interval_sec is not None
        else getattr(ctx.session, "loop_interval_sec", 15.0)
    )

    # ML config is no longer part of session updates

    # Validate Profile
    if payload.profile_name is not None:
        try:
            safe = sanitize_filename(next_profile)
        except ValueError:
            return ApiEnvelope(data=None, error="Invalid profile name")

        if safe != next_profile or not next_profile:
            return ApiEnvelope(data=None, error="Invalid profile name")

        # Allow "default" or check existence in registry
        if next_profile != "default" and next_profile not in ctx.config.profiles:
            return ApiEnvelope(data=None, error=f"Profile '{next_profile}' not found")

    # Enforce Mode Safety
    execution_config = ctx.config.execution
    if next_mode != execution_config.mode:
        return ApiEnvelope(
            data=None,
            error=f"System execution mode mismatch (expected {execution_config.mode}). Use /api/system/mode first.",
        )

    if next_mode == "live":
        if not getattr(execution_config, "allow_live_trading", False):
            return ApiEnvelope(
                data=None,
                error="Live trading not enabled. Use system mode switch with authentication first.",
            )

    old_profile = getattr(ctx.session, "profile_name", None)

    # Apply to Memory
    ctx.session.profile_name = next_profile
    ctx.session.mode = next_mode
    ctx.session.loop_interval_sec = next_loop
    # ML enabled status is just reflecting current config state in memory, not set here

    ctx.session.active = False  # Ensure remains false

    ctx.config.session.profile_name = next_profile
    ctx.config.session.mode = next_mode
    ctx.config.session.loop_interval_sec = next_loop
    ctx.config.session.active = False

    # Persist
    config_dir = get_config_dir()
    _persist_session_config_to_main_config(
        config_dir,
        profile_name=next_profile,
        mode=next_mode,
        loop_interval_sec=next_loop,
    )
    dump_runtime_overrides(ctx.config, session=ctx.session, sections={"session"})

    # Trigger Hot-Swap if Profile Changed
    if next_profile != old_profile:
        ctx.reinitialize_event.set()

    return ApiEnvelope(data=_session_payload(ctx), error=None)


@router.post("/session/start", response_model=ApiEnvelope[SessionStatePayload])
async def start_session(request: Request) -> ApiEnvelope[SessionStatePayload]:
    ctx = _context(request)
    _check_setup_mode(ctx)

    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.reinitialize_event.is_set():
        return ApiEnvelope(
            data=None, error="System is reloading. Please try again in a moment."
        )

    if ctx.session.active:
        return ApiEnvelope(data=_session_payload(ctx), error=None)

    execution_config = ctx.config.execution
    current_mode = getattr(ctx.session, "mode", "paper")
    profile_name = getattr(ctx.session, "profile_name", "default")

    # Guard: Mode Consistency
    if current_mode != execution_config.mode:
        return ApiEnvelope(
            data=None,
            error=f"System execution mode mismatch (expected {execution_config.mode}). Use /api/system/mode and /api/system/session/config.",
        )

    # Guard: Live Mode
    if current_mode == "live":
        if not getattr(execution_config, "allow_live_trading", False):
            return ApiEnvelope(
                data=None,
                error="Live trading not enabled. Use system mode switch with authentication first.",
            )

    # Update runtime session state from config source of truth
    ctx.session.ml_enabled = ctx.config.ml.enabled

    # Activate Memory Only
    ctx.session.active = True
    # ctx.config.session.active stays False to prevent auto-resume on restart
    # No disk writes here

    if current_mode == "live" and hasattr(
        ctx.execution_service, "_emit_live_readiness_checklist"
    ):
        ctx.execution_service._emit_live_readiness_checklist()

    logger.info(
        "Session started",
        extra=build_request_log_extra(
            request,
            event="session_started",
            profile=profile_name,
            mode=current_mode,
        ),
    )

    return ApiEnvelope(data=_session_payload(ctx), error=None)


@router.post("/session/stop", response_model=ApiEnvelope[SessionStatePayload])
async def stop_session(request: Request) -> ApiEnvelope[SessionStatePayload]:
    ctx = _context(request)
    _check_setup_mode(ctx)

    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    ctx.session.active = False
    # No disk writes here

    logger.info(
        "Session stopped",
        extra=build_request_log_extra(request, event="session_stopped"),
    )

    return ApiEnvelope(data=_session_payload(ctx), error=None)


@router.get("/profiles", response_model=ApiEnvelope[list[ProfileSummaryPayload]])
async def list_profiles(request: Request) -> ApiEnvelope[list[ProfileSummaryPayload]]:
    try:
        ctx = _context(request)
        # Removed _check_setup_mode(ctx) to allow access in setup mode
        profiles = [
            ProfileSummaryPayload(name=name, description=cfg.description)
            for name, cfg in ctx.config.profiles.items()
        ]
        return ApiEnvelope(data=profiles, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to list profiles",
            extra=build_request_log_extra(request, event="profiles_fetch_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/profiles", response_model=ApiEnvelope[dict])
async def create_profile(
    payload: ProfileCreatePayload, request: Request
) -> ApiEnvelope[dict]:
    """
    Creates a new profile.
    """
    ctx = _context(request)
    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot create profile while session is active"
        )

    config_dir = get_config_dir()
    main_config_path = config_dir / "config.yaml"

    try:
        safe_name = sanitize_filename(payload.name)
    except ValueError as e:
        return ApiEnvelope(data=None, error=str(e))

    profile_filename = f"{safe_name}.yaml"
    profile_path = config_dir / "profiles" / profile_filename

    if profile_path.exists():
        return ApiEnvelope(
            data=None, error=f"Profile file '{profile_filename}' already exists"
        )

    try:
        # 1. Prepare Base Config
        base_config = payload.base_config or {}

        # 2. UI Key Boundary Check
        if "ui" in base_config:
            if not isinstance(base_config["ui"], dict):
                return ApiEnvelope(data=None, error="UI config must be a dictionary")

            allowed_ui_keys = {"refresh_intervals"}
            present_ui_keys = set(base_config["ui"].keys())
            invalid_keys = present_ui_keys - allowed_ui_keys

            if invalid_keys:
                return ApiEnvelope(
                    data=None,
                    error=f"UI server settings cannot be set in profile base_config; only ui.refresh_intervals is allowed. Found: {invalid_keys}",
                )

        # 3. Security: Prevent setting restricted execution keys in new profiles
        execution_payload = base_config.get("execution", {})
        restricted_keys = {
            "mode",
            "allow_live_trading",
            "validate_only",
            "paper_tests_completed",
        }
        for key in restricted_keys:
            if key in execution_payload:
                return ApiEnvelope(
                    data=None,
                    error=f"Execution '{key}' cannot be set via base_config. It is controlled by system state.",
                )

        # Ensure minimal structure using the declared default mode
        if "execution" not in base_config:
            base_config["execution"] = {}

        base_config["execution"]["mode"] = payload.default_mode
        base_config["execution"]["allow_live_trading"] = False
        base_config["execution"]["validate_only"] = payload.default_mode != "live"

        if payload.default_mode == "live":
            return ApiEnvelope(
                data=None,
                error="Cannot create profile with default mode 'live'. Use 'paper' or 'dry_run' and upgrade later.",
            )

        # 4. FULL LOADER VALIDATION (Effective Config)
        try:
            # Load Main
            existing_main = _load_yaml_mapping(main_config_path)

            # Resolve Env
            effective_env = _resolve_effective_env(None, str(main_config_path))
            env_config_path = main_config_path.parent / f"config.{effective_env}.yaml"
            env_config = _load_yaml_mapping(env_config_path)

            # Build Effective
            merged = deep_merge_dicts(existing_main, env_config)
            merged = deep_merge_dicts(merged, base_config)

            # No runtime overrides yet for new profile

            # Parse
            parse_app_config(
                merged,
                config_path=main_config_path,
                effective_env=effective_env,
            )
        except Exception as e:
            return ApiEnvelope(data=None, error=f"Profile configuration invalid: {e}")

        # 5. Write Profile File
        atomic_write(profile_path, base_config, dump_func=yaml.safe_dump)

        # 6. Update Main Config Registry
        backup_file(main_config_path)

        with open(main_config_path, "r") as f:
            main_data = yaml.safe_load(f) or {}

        profiles = main_data.get("profiles", {})
        profiles[safe_name] = {
            "name": safe_name,
            "description": payload.description,
            "config_path": str(Path("profiles") / profile_filename),
            "credentials_path": "",
            "default_mode": payload.default_mode,
        }
        main_data["profiles"] = profiles

        atomic_write(main_config_path, main_data, dump_func=yaml.safe_dump)

        # 7. Trigger Reload
        ctx.reinitialize_event.set()

        logger.info(
            "Profile created",
            extra=build_request_log_extra(
                request, event="profile_created", profile_name=safe_name
            ),
        )

        return ApiEnvelope(
            data={"name": safe_name, "path": str(profile_path)}, error=None
        )

    except Exception as exc:
        logger.exception(
            "Failed to create profile",
            extra=build_request_log_extra(request, event="profile_create_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/metrics", response_model=ApiEnvelope[SystemMetricsPayload])
async def system_metrics(request: Request) -> ApiEnvelope[SystemMetricsPayload]:
    try:
        ctx = _context(request)
        _check_setup_mode(ctx)
        metrics = ctx.metrics
        # Thin wrapper around the shared SystemMetrics snapshot to avoid duplicating logic.
        snapshot = metrics.snapshot()
        payload = SystemMetricsPayload(**snapshot)
        return ApiEnvelope(data=payload, error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch system metrics",
            extra=build_request_log_extra(request, event="system_metrics_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.get("/config", response_model=ApiEnvelope[dict])
async def get_config(request: Request) -> ApiEnvelope[dict]:
    try:
        ctx = _context(request)
        _check_setup_mode(ctx)
        return ApiEnvelope(data=_redacted_config(ctx.config), error=None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Failed to fetch config",
            extra=build_request_log_extra(request, event="config_fetch_failed"),
        )
        return ApiEnvelope(data=None, error=str(exc))


@router.post("/mode", response_model=ApiEnvelope[dict])
async def set_execution_mode(
    payload: ModeChangePayload, request: Request
) -> ApiEnvelope[dict]:
    ctx = _context(request)
    _check_setup_mode(ctx)

    if ctx.config.ui.read_only:
        return ApiEnvelope(data=None, error="UI is in read-only mode")

    if ctx.session.active:
        return ApiEnvelope(
            data=None, error="Cannot change mode while session is active"
        )

    new_mode = payload.mode
    execution_config = ctx.config.execution
    current_mode = execution_config.mode

    if new_mode == current_mode:
        return ApiEnvelope(
            data={
                "mode": current_mode,
                "validate_only": execution_config.validate_only,
            },
            error=None,
        )

    account_id = ctx.session.account_id

    # GUARD: Switching TO live mode
    if new_mode == "live":
        # Only require password + confirmation if we aren't already allowed to trade live.
        # This allows switching back and forth if already authenticated/unlocked.
        if not execution_config.allow_live_trading:
            # Check credentials and phrase
            if not payload.password or not payload.confirmation:
                return ApiEnvelope(
                    data=None,
                    error="Live mode requires password and confirmation phrase",
                )

            if payload.confirmation != "ENABLE LIVE TRADING":
                return ApiEnvelope(data=None, error="Invalid confirmation phrase")

            try:
                secrets_path = resolve_secrets_path(None, account_id)
                unlock_secrets(payload.password, secrets_path=secrets_path)
                # Ensure we persist this for reload
                set_session_master_password(account_id, payload.password)
            except Exception:
                logger.warning(
                    "Live mode auth failed",
                    extra=build_request_log_extra(request, event="live_auth_failed"),
                )
                return ApiEnvelope(data=None, error="Invalid password")

        # Persist this permission so reload picks it up?
        # Yes, we need to update the config.
        # But wait, config updates should happen via /config/apply or profile?
        # Here we do a localized update to execution config.
        # Ideally we use atomic write here too.
        # Let's piggyback on config logic or duplicate simple update.
        # We need to update 'execution.allow_live_trading' in the config file.

        config_dir = get_config_dir()
        profile_name = ctx.session.profile_name
        target_path = None

        if profile_name:
            profiles_entry = ctx.config.profiles.get(profile_name)
            if profiles_entry:
                p_path = Path(profiles_entry.config_path)
                if not p_path.is_absolute():
                    p_path = config_dir / p_path
                # Aegis: Unrestricted profile config path -> bounded to config directory (no exploit details)
                if (
                    p_path.resolve().is_relative_to(config_dir.resolve())
                    and p_path.exists()
                ):
                    target_path = p_path

        if not target_path:
            target_path = config_dir / "config.yaml"

        # Load, update, save
        try:
            with open(target_path, "r") as f:
                data = yaml.safe_load(f) or {}

            exec_sec = data.get("execution", {})
            exec_sec["mode"] = "live"
            exec_sec["validate_only"] = False
            exec_sec["allow_live_trading"] = True
            # NOTE: We DO NOT set paper_tests_completed=True automatically anymore.

            data["execution"] = exec_sec

            backup_file(target_path)
            atomic_write(target_path, data, dump_func=yaml.safe_dump)

        except Exception as e:
            return ApiEnvelope(
                data=None, error=f"Failed to persist live mode settings: {e}"
            )

    # For other modes, we might just update runtime state or config too?
    # Usually mode change persists.
    # NOTE: Re-implementing generic persistence for mode change:
    config_dir = get_config_dir()
    profile_name = ctx.session.profile_name
    target_path = None
    if profile_name:
        profiles_entry = ctx.config.profiles.get(profile_name)
        if profiles_entry:
            p_path = Path(profiles_entry.config_path)
            if not p_path.is_absolute():
                p_path = config_dir / p_path
            # Aegis: Unrestricted profile config path -> bounded to config directory (no exploit details)
            if (
                p_path.resolve().is_relative_to(config_dir.resolve())
                and p_path.exists()
            ):
                target_path = p_path
    if not target_path:
        target_path = config_dir / "config.yaml"

    try:
        with open(target_path, "r") as f:
            data = yaml.safe_load(f) or {}
        exec_sec = data.get("execution", {})
        exec_sec["mode"] = new_mode
        exec_sec["validate_only"] = new_mode != "live"
        if new_mode == "live":
            exec_sec["allow_live_trading"] = True
        data["execution"] = exec_sec
        backup_file(target_path)
        atomic_write(target_path, data, dump_func=yaml.safe_dump)
    except Exception as e:
        return ApiEnvelope(data=None, error=f"Failed to persist mode: {e}")

    # Update in-memory state so subsequent calls reflect the change immediately
    # We do this AFTER successful persistence to avoid split-brain if write fails.
    execution_config.mode = new_mode
    execution_config.validate_only = new_mode != "live"
    if new_mode == "live":
        execution_config.allow_live_trading = True

    ctx.session.mode = new_mode
    if hasattr(ctx.config, "session"):
        ctx.config.session.mode = new_mode

    # If the adapter is already initialized, update its config reference too
    if ctx.execution_service and hasattr(ctx.execution_service, "adapter"):
        adapter_conf = getattr(ctx.execution_service.adapter, "config", None)
        if adapter_conf:
            adapter_conf.mode = new_mode
            adapter_conf.validate_only = new_mode != "live"
            if new_mode == "live":
                adapter_conf.allow_live_trading = True

    # Trigger reload
    ctx.reinitialize_event.set()

    logger.info(
        "Execution mode updated",
        extra=build_request_log_extra(
            request,
            event="mode_changed",
            old_mode=current_mode,
            new_mode=new_mode,
        ),
    )

    return ApiEnvelope(
        data={
            "mode": new_mode,
            "validate_only": execution_config.validate_only,
            "reloading": True,
        },
        error=None,
    )


@router.post("/credentials/validate", response_model=ApiEnvelope[dict])
async def validate_credentials(
    payload: CredentialPayload, request: Request
) -> ApiEnvelope[dict]:
    """Validate API credentials by pinging a lightweight private Kraken endpoint."""

    missing = [
        field_name
        for field_name, value in (
            ("apiKey", payload.apiKey),
            ("apiSecret", payload.apiSecret),
            ("region", payload.region),
        )
        if not value or not value.strip()
    ]

    if missing:
        return ApiEnvelope(
            data={"valid": False},
            error="apiKey, apiSecret, and region are required.",
        )

    try:
        result = validation_mod.validate_credentials(
            payload.apiKey.strip(),
            payload.apiSecret.strip(),
            region=payload.region.strip(),
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception(
            "Unexpected error during credential validation",
            extra=build_request_log_extra(
                request, event="credential_validation_failed", error=str(exc)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error=(
                "Unexpected error while validating credentials. Please retry or check server logs."
            ),
        )

    if result.status is CredentialStatus.LOADED and result.validated:
        return ApiEnvelope(data={"valid": True}, error=None)

    error = result.error

    if isinstance(error, AuthError):
        logger.warning(
            "Credential validation failed",
            extra=build_request_log_extra(
                request, event="credential_validation_auth_error", error=str(error)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Authentication failed. Please verify your API key/secret.",
        )

    if isinstance(error, ServiceUnavailableError):
        logger.warning(
            "Credential validation unavailable",
            extra=build_request_log_extra(
                request, event="credential_validation_unavailable", error=str(error)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Kraken is unavailable or could not be reached. Please retry.",
        )

    if isinstance(error, KrakenAPIError):
        logger.warning(
            "Credential validation failed with API error",
            extra=build_request_log_extra(
                request, event="credential_validation_api_error", error=str(error)
            ),
        )
        return ApiEnvelope(
            data={"valid": False},
            error="Authentication failed. Please verify your API key/secret.",
        )

    logger.warning(
        "Credential validation failed with unexpected service error",
        extra=build_request_log_extra(
            request,
            event="credential_validation_unknown_service_error",
            error=str(error),
        ),
    )
    return ApiEnvelope(
        data={"valid": False},
        error=(
            "Unexpected error while validating credentials. Please retry or check server logs."
        ),
    )
