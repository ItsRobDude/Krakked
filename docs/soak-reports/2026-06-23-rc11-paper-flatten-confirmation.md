# RC11 Paper Emergency-Flatten Confirmation: 2026-06-23

Date: 2026-06-23 PDT
Host: Tower / Unraid
Image: `ghcr.io/itsrobdude/krakked:v0.1.1-rc.11`
Build SHA: `8906bba7a505a63ad4544475c1e5f1f3f1f03f84`
Profile: `paper-flatten-confirm-2026-06-23-rc11`
Mode: paper
ML: disabled

## Verdict

The controlled `v0.1.1-rc.11` paper emergency-flatten confirmation passed.

The runtime used the intended pinned image and build SHA, seeded paper BTC/ETH
positions through the deployed paper runtime, resumed with
`session.emergency_flatten=true`, submitted paper market close orders with
non-null fill prices, inserted paper trades, reduced the synthetic wallet back
to USD, and cleared emergency intent.

This closes the paper-runtime defect exposed by the `v0.1.1-rc.10` forward
soak, where paper market flatten orders were marked filled with
`avg_fill_price=null` and did not reduce synthetic wallet positions.

Scope boundary:

- This proves the deployed paper runtime path for emergency flatten.
- It does not prove live Kraken order submission.
- It does not prove real Kraken TradesHistory/Ledgers reconciliation.
- The deterministic fake-Kraken live-config tests remain the live-adapter and
  live account-truth gate proof.

## Run Inputs

- Runtime URL: `http://192.168.50.78:8088`.
- Runtime source: published image.
- Expected image: `ghcr.io/itsrobdude/krakked:v0.1.1-rc.11`.
- Expected SHA: `8906bba7a505a63ad4544475c1e5f1f3f1f03f84`.
- Confirmation profile: `paper-flatten-confirm-2026-06-23-rc11`.
- Confirmation profile config path:
  `/krakked/config/profiles/paper-flatten-confirm-2026-06-23-rc11.yaml`.
- Confirmation portfolio DB path:
  `/krakked/state/paper-flatten-confirm-2026-06-23-rc11/portfolio.db`.
- Summary artifact:
  `/krakked/state/paper-flatten-confirm-2026-06-23-rc11/flatten-confirm-summary.json`.
- Monitor artifact:
  `/mnt/user/appdata/krakked/state/paper-flatten-confirm-2026-06-23-rc11/monitor.jsonl`.

Before the confirmation drill, the `v0.1.1-rc.11` decision soak profile was
stopped and archived:

- Archived profile: `decision-soak-2026-06-23-rc11-forward`.
- Archive summary:
  `/krakked/state/decision-soak-2026-06-23-rc11-forward/archive-summary.json`.
- Archive evidence: 600 monitor samples, no decisions, no execution orders, no
  trades, portfolio sync OK in every sample, and final equity/cash still
  `$10,000`.

## Seed Method

The confirmation used the real deployed paper runtime path, not direct DB
mutation.

The temporary confirmation profile enabled only `dca_overlay` with the normal
paper runtime. The paper session produced two filled paper buy orders:

- `BTC/USD` buy `0.00160163` at average fill `62749.0`.
- `ETH/USD` buy `0.06017263` at average fill `1670.2`.

Before flatten:

- Open orders: `0`.
- Cash: about `$9,798.9990`.
- Equity: about `$9,998.9996`.
- Unrealized PnL: about `-$1.0004`.
- Sellable positions:
  - `XBTUSD` base `0.00160163`.
  - `ETHUSD` base `0.06017263`.

## Background Emergency Resume Drill

The runtime was then stopped inactive, the persisted session was armed with
`emergency_flatten=true`, and the container was recreated. This specifically
tested the background emergency-flatten resume branch, not only the
`/api/execution/flatten_all` route.

Observed log events:

- `emergency_flatten_active`.
- `emergency_flatten_cleared_after_execution`.

No `ERROR`, `CRITICAL`, `Traceback`, `synthetic_fill_price_missing`,
`emergency_flatten_no_progress_halted`, or `emergency_flatten_deferred_halted`
entries were observed during the confirmation window.

The background flatten submitted two filled paper market sell orders:

- `XBTUSD` sell `0.00160163` at average fill `62486.45`.
- `ETHUSD` sell `0.06017263` at average fill `1663.375`.

Both filled close orders had:

- non-null `avg_fill_price`;
- non-zero `cumulative_base_filled`;
- no `last_error`.

## SQLite Evidence

Final confirmation DB counts:

- `decisions`: `2`.
- `execution_plans`: `1`.
- `execution_results`: `2`.
- `execution_orders`: `4`.
- `execution_order_events`: `4`.
- `trades`: `4`.
- `balance_snapshots`: `3`.
- `ledger_entries`: `0` (expected in paper mode).

Order evidence:

- Order status counts: `filled=4`.
- Order side counts: `buy=2`, `sell=2`.
- Filled orders missing price: `0`.
- Filled orders with zero volume: `0`.

Trade evidence:

- `paper-trade-5338a218-56f8-5111-bc66-d5cb04b656e2`:
  `BTC/USD` buy `0.00160163` at `62749.0`.
- `paper-trade-2e1016d6-5ccd-589d-834b-9ba3063732f1`:
  `ETH/USD` buy `0.06017263` at `1670.2`.
- `paper-trade-d665dba0-8f41-5fbb-b9e9-3101dd32e34c`:
  `XBTUSD` sell `0.00160163` at `62486.45`.
- `paper-trade-20c23be5-b7e8-5b8e-a0e6-b76ab4295dcc`:
  `ETHUSD` sell `0.06017263` at `1663.375`.

Balance snapshots showed the intended transition:

1. Initial: `USD=10000.0`.
2. After seed buys:
   `USD=9798.998992504`, `BTC=0.00160163`, `ETH=0.06017263`.
3. After emergency flatten:
   `USD=9999.168813843751`, `BTC=0.0`, `ETH=0.0`.

The final realized paper loss was about `$0.8312`, which is expected from the
seed buy prices versus close prices and is not a failure of the control path.

## Final Runtime State

After confirmation:

- Session active: `false`.
- Lifecycle: `ready`.
- `emergency_flatten`: `false`.
- Open orders: `[]`.
- Selected runtime profile was restored to
  `decision-soak-2026-06-23-rc11-forward`.
- Active DB path after restore:
  `/krakked/state/decision-soak-2026-06-23-rc11-forward/portfolio.db`.

The confirmation profile's positions endpoint still showed zero-size BTC/ETH
rows marked as dust/untradeable. Practical exposure was flat because the paper
wallet held `BTC=0.0` and `ETH=0.0`; the zero-size rows are a display cleanup
candidate, not a failed flatten.

## What This Run Proved

- The rc11 paper adapter no longer phantom-fills market close orders without a
  usable price.
- Paper emergency market close orders can fill with non-null average prices.
- Filled paper close orders are ingested into paper trades.
- The synthetic wallet reduces BTC/ETH balances to zero.
- Background emergency-flatten resume clears emergency intent once flat.
- The deployed runtime remains tied to the intended image/SHA/profile/DB.

## What This Run Did Not Prove

- Live Kraken order submission.
- Real exchange fills.
- Real TradesHistory/Ledgers reconciliation.
- Multi-account or multi-exchange flatten behavior.
- Strategy edge or profitability.

## Recommendation

Treat paper emergency flatten as restored for the normal paper runtime path.
The remaining money-safety work should stay focused on the already documented
broader live-capital gaps: validate-only drill criteria, tiny live-smoke
criteria, broader process-death cases, dead-man and alert proofs, and continued
separation between paper runtime evidence and live Kraken reconciliation
claims.
