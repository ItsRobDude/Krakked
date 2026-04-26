# Krakked Agent Guardrails

Use this as the first stop for future Codex/agent work in this repo. Keep changes narrow, evidence-backed, and aligned with operator trust.

## Current Safety Contract

- Normal paper mode uses a persistent synthetic Krakked wallet, not Kraken live balances.
- In raw config, normal paper mode should use `execution.validate_only=false`. The config loader may normalize stale paper configs back to false.
- Live order submission is blocked unless all three gates are intentionally open: `execution.mode="live"`, `execution.validate_only=false`, and `execution.allow_live_trading=true`.
- Safety or operator summaries may describe paper mode as effectively safe/validate-only because it cannot submit live orders. Do not confuse that operator meaning with the raw config flag.
- `krakked run-once` is a special helper path: it forces paper/validate-only behavior regardless of user config so it cannot transmit live orders.

## Test-Writing Rules

- Prefer real `AppConfig` dataclasses over `MagicMock(spec=AppConfig)`. Dataclass instance fields are not always available on a spec mock until explicitly attached.
- If mocking `PortfolioService`, explicitly stub cached reads used by strategy, risk, and UI paths: cached equity, positions, exposures, drift status, strategy performance, recent decisions, and snapshots as needed.
- Isolate machine state in secrets tests. Patch env vars, session master passwords, and keyring-backed saved passwords when testing missing-password behavior.
- Avoid truthy `MagicMock` values in math or lifecycle branches. Return real lists, dataclasses, `SimpleNamespace` values, or `None`.
- Tests that exercise live/Kraken pagination or ledger ingestion must opt into non-paper execution config so they do not silently take the synthetic paper-wallet path.

## Verification Defaults

- Run targeted tests first, for example `poetry run pytest tests/ui/test_system_routes.py`.
- Run full Python coverage when shared config, safety, portfolio, strategy, or UI route behavior changes: `poetry run pytest`.
- Run `npm run build` in `ui` after frontend or API-shape changes.
- Finish with `git diff --check`.
- If `poetry` is not on PATH on this Windows machine, try `C:\Users\Rob\AppData\Roaming\Python\Python311\Scripts\poetry.exe`.
