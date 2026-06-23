# Decision-Soak Forward Paper Report: 2026-06-23

Date: 2026-06-23 PDT
Host: Tower / Unraid
Image: `ghcr.io/itsrobdude/krakked:v0.1.1-rc.10`
Build SHA: `3325c6d6268850bac46900892c3b3c79cff1757f`
Profile: `decision-soak-2026-06-22-rc10-forward`
Mode: paper
ML: disabled

## Verdict

The `v0.1.1-rc.10` forward decision soak passed the paper decision-loop proof.
It ran on the intended image and isolated profile, stayed observable for roughly
twelve hours, produced strategy-generated risk decisions, submitted filled paper
orders, persisted paper trades and snapshots, and kept deployment/runtime
provenance legible.

The follow-up paper emergency-flatten drill did not pass. The API route
submitted the expected reduce/close market orders, but paper portfolio state did
not go flat. The background emergency-flatten resume path retried repeatedly
until the container was stopped for containment. Treat this as a paper-mode
operator-control defect, not a live-capital proof failure.

Follow-up: the `v0.1.1-rc.11` controlled paper emergency-flatten confirmation
repeated the deployed paper runtime path successfully. See
[`2026-06-23-rc11-paper-flatten-confirmation.md`](./2026-06-23-rc11-paper-flatten-confirmation.md).

Scope boundary:

- This soak proves paper runtime/operator behavior with synthetic paper fills.
- It does not prove live Kraken order submission.
- It does not prove real Kraken TradesHistory/Ledgers reconciliation.
- It does not exercise live account-truth opening-risk gates.
- The deterministic fake-Kraken live-config tests remain the live-gate bridge
  proof for strategy-generated opening-risk actions.

## Run Inputs

- Deployment preflight: completed before session start.
- Runtime URL: `http://192.168.50.78:8088`.
- Runtime source: published image.
- Expected image: `ghcr.io/itsrobdude/krakked:v0.1.1-rc.10`.
- Expected SHA: `3325c6d6268850bac46900892c3b3c79cff1757f`.
- Active profile: `decision-soak-2026-06-22-rc10-forward`.
- Active profile config path:
  `/krakked/config/profiles/decision-soak-2026-06-22-rc10-forward.yaml`.
- Active portfolio DB path:
  `/krakked/state/decision-soak-2026-06-22-rc10-forward/portfolio.db`.
- Monitor JSONL:
  `/mnt/user/appdata/krakked/state/decision-soak-20260622-rc10-forward.jsonl`.

The session used a multi-strategy paper profile:

- `trend_core`: enabled, research-stage starter strategy.
- `majors_mean_rev`: enabled for no-signal diagnostics.
- `rs_rotation`: enabled as a research-only event/diagnostic source.

## Monitor Window

- First sample: `2026-06-23T01:18:49Z`
  (2026-06-22 18:18:49 PDT).
- Last sample: `2026-06-23T13:46:49Z`
  (2026-06-23 06:46:49 PDT).
- Samples: `743`.
- Endpoint success: `742 / 743` for every monitored endpoint.
- The one failed sample occurred after the container was intentionally stopped
  to contain the emergency-flatten retry loop.
- Session active samples before containment: `738`.
- Monitor file size at containment: about `36M`.

Runtime identity was stable in every successful monitor sample:

- Image tag: `v0.1.1-rc.10`.
- Build SHA: `3325c6d6268850bac46900892c3b3c79cff1757f`.
- Deployment drift detected: `false`.
- Profile: `decision-soak-2026-06-22-rc10-forward`.
- Portfolio DB path:
  `/krakked/state/decision-soak-2026-06-22-rc10-forward/portfolio.db`.

## Runtime Health

- Portfolio sync: OK in all successful samples.
- Portfolio sync reason: `null` in all successful samples.
- Drift detected: `false` in all successful samples.
- Open orders: `0` in all samples before flatten.
- Market data:
  - `stale_pairs=0` in `703` samples.
  - `stale_pairs=1` in `39` samples.
  - Max observed staleness: about `275` seconds.
  - Stale windows recovered; latest pre-containment health was streaming.

The container-level Docker healthcheck was still noisy on Unraid, but app-level
HTTP health remained authoritative for this run.

## Strategy Evidence

`trend_core` produced the primary decision stream:

- `intents_emitted`: `3` samples.
- `intents_score_filtered`: `12` samples.
- `deferred_no_new_bar`: `726` samples.
- Maximum observed intents in a strategy summary: `5`.
- Maximum observed actions after scoring: `3`.
- Maximum observed score-filtered candidates: `4`.

`rs_rotation` produced the expected score-filtered diagnostic:

- `intents_score_filtered`: `1` sample.
- `no_signal`: `3` samples.
- `deferred_no_new_bar`: `737` samples.
- The score-filtered sample included two candidates, both rejected before risk:
  - `SOL/USD` long enter, relative return about `-2.47%`,
    confidence `0`, score `0`, threshold `0.05`,
    `filter_reason="below_score_threshold"`.
  - `ETH/USD` long enter, relative return about `-2.90%`,
    confidence `0`, score `0`, threshold `0.05`,
    `filter_reason="below_score_threshold"`.

`majors_mean_rev` stayed diagnostic-only:

- `no_signal`: `14` samples.
- `deferred_no_new_bar`: `727` samples.
- Messages distinguished "below band but regime is not mean reverting" from
  "not below the lower mean-reversion band."

Finding: the score-filter truth work from `v0.1.1-rc.10` behaved as intended.
Zero-conviction candidates were visible as filtered candidates, not runtime
silence.

## OMS, Risk, And Portfolio Evidence

The persisted DB after containment contained:

- `execution_plans`: `697`.
- `execution_results`: `11`.
- `execution_orders`: `20`.
- `execution_order_events`: `20`.
- `decisions`: `8`.
- `trades`: `4`.
- `snapshots`: `14`.
- `balance_snapshots`: `3`.
- `ledger_entries`: `0` (expected in paper mode).

Before the emergency-flatten attempt, the normal trading loop produced:

- `3` execution results.
- `4` filled paper orders.
- `4` persisted paper trades.
- `8` persisted risk decisions.

Normal paper orders:

- `plan_1782177547` at `2026-06-23T01:19:15Z`:
  - `BTC/USD` buy `0.00725869` at average fill `64429.9`.
  - `ETH/USD` buy `0.02003070` at average fill `1738.49`.
- `plan_1782187301` at `2026-06-23T04:01:46Z`:
  - `BTC/USD` sell/reduce `0.00239118` at average fill `63671.5`.
  - `ETH/USD` buy/increase `0.08893612` at average fill `1737.17`.
- `plan_1782201695` at `2026-06-23T08:01:42Z`:
  - successful execution result with no orders.

Latest pre-flatten paper positions:

- `XBTUSD`: base `0.00486751`, average entry `64429.9`.
- `ETHUSD`: base about `0.10896681`, average entry `1737.41`.

Latest pre-flatten sampled portfolio summary:

- Cash: about `$9,495.25`.
- Equity: about `$9,978.81`.
- Realized PnL: about `-$1.81`.
- Unrealized PnL: about `-$19.38`.
- Baseline: `paper_wallet`.

Negative PnL is not a failure for this proof. These are research-stage starter
strategies; this run evaluated runtime plumbing and operator truth, not alpha.

## Risk Diagnostic Finding

The DB exposed one operator-truth issue in the risk decision schema and display
path.

Some decisions were `blocked=false` and `clamped=true`, but the persisted
`block_reason` column still contained the cap reason. For example:

- `ETH/USD` at `2026-06-23T01:19:07Z`:
  - `blocked=false`.
  - raw decision reason: `Clamped: Strategy trend_core budget exceeded
    (913.32 > 500.00)`.
  - `raw_clamped=true`.
  - `block_reason` column: `Strategy trend_core budget exceeded
    (913.32 > 500.00)`.

This is not a money-safety bug, but it is an operator-truth gap. Clamped reasons
should be surfaced separately from blocked reasons so the UI and reports do not
make a clamped action look blocked.

## Controlled Paper Emergency-Flatten Attempt

The paper emergency flatten was triggered after the long soak using:

- Endpoint: `POST /api/execution/flatten_all`.
- Confirmation phrase: `FLATTEN ALL`.
- Time: `2026-06-23T13:43:34Z`.

Preconditions:

- Session active.
- Health OK.
- Portfolio sync OK.
- Open orders empty.
- Sellable paper positions present in `XBTUSD` and `ETHUSD`.

Expected behavior:

1. Cancel all open orders.
2. Refresh/reconcile open orders.
3. Sync portfolio.
4. Build reduce/close actions for sellable paper positions.
5. Submit paper close orders.
6. Ingest filled paper orders into the synthetic wallet.
7. Clear emergency intent when positions are flat.

Observed behavior:

- The route called cancel-all first.
- Open orders remained empty.
- The API flatten plan submitted two filled paper market sell orders:
  - `XBTUSD` sell `0.00486751`.
  - `ETHUSD` sell `0.10896681`.
- The API response reported `success=true`, no warnings, and no errors.
- Both filled market orders had `avg_fill_price=null`.
- No new paper trades were inserted; `trades` remained `4`.
- The paper positions remained unchanged.
- `session.emergency_flatten` stayed `true`.
- The background emergency-flatten resume path retried repeatedly.

Containment:

- The session was stopped through `/api/system/session/stop`, but emergency
  flatten intentionally runs while inactive, so retries continued.
- The container was then stopped to prevent further paper retry noise.
- Final container state:
  `krakked-krakked-1 Exited (0)`.

Final DB evidence after containment:

- Flatten plans: `8`.
- Flatten orders: `16`.
- Filled flatten orders: `16`.
- Flatten orders with `avg_fill_price=null`: `16`.
- Persisted paper trades after all flatten attempts: still `4`.
- Paper positions after all flatten attempts: still `XBTUSD` and `ETHUSD`.

Finding: paper emergency flatten currently submits filled paper market orders but
does not reduce synthetic paper positions. The likely proximate cause is that
paper market flatten orders do not carry an executable average fill price, and
paper filled-order ingestion skips orders without a usable price. The next code
slice should fix market-order paper fill pricing and add a regression that a
paper flatten market order creates paper trades, updates cash, reduces
positions, and clears emergency intent.

Operational warning: do not restart this profile with the current runtime state
unless the intent is to reproduce the retry loop. The profile still has
`emergency_flatten=true` and non-flat paper positions.

## What This Run Proved

- Correct pinned-image deployment on `v0.1.1-rc.10`.
- Runtime provenance stayed stable.
- The active profile and profile-isolated DB path were visible and correct.
- A long paper session stayed observable across multiple strategy bars.
- Market data was mostly healthy and recovered from transient stale-pair noise.
- Score-filtered candidates were legible.
- `trend_core` produced real strategy-generated paper decisions.
- Risk caps blocked and clamped actions.
- OMS produced filled paper orders.
- Paper trades and snapshots persisted for normal limit-order paper fills.
- Portfolio state updated after normal paper fills.

## What This Run Did Not Prove

- Live Kraken order submission.
- Real exchange fills.
- Real TradesHistory/Ledgers reconciliation.
- Live account-truth gates.
- Strategy edge or profitability.
- Emergency flatten success in paper mode.

## Recommendations

1. Keep the `v0.1.1-rc.11` controlled paper flatten confirmation as the
   follow-up proof that paper market close orders now carry prices, write paper
   trades, update synthetic wallet balances/positions, and clear emergency
   intent.
2. Separate clamped and blocked diagnostic fields in persisted/API risk
   decisions.
3. Keep the `v0.1.1-rc.10` forward soak as a passed paper decision-loop proof,
   with the explicit historical caveat that its flatten attempt failed and was
   resolved by the rc.11 confirmation.
4. Do not use this profile for further automation unless its emergency flag and
   paper positions are intentionally handled.
