# Paper Validation Report: 2026-06-20

Date: 2026-06-20 PDT
Host: Tower / Unraid
Image: `ghcr.io/itsrobdude/krakked:v0.1.1-rc.8`
Build SHA: `8b6bd577901a3ff8bf50f9766a546193f9bb32ed`
Profile: `paper-soak-2026-06-18`
Mode: paper
ML: disabled

## Verdict

This short pinned-image paper validation is the first real paper-session run
that produced useful strategy and execution evidence. It proved that the
post-account-truth-gate image can start on Unraid, hold an active paper
session, preserve runtime provenance, stream mostly fresh market data, explain
strategy deferrals/no-signal decisions, emit strategy intents, route through
risk/OMS, create dry-run orders, record paper trades, and update portfolio
positions.

It did not prove live exchange submission, real Kraken fills, live ledger
ingestion, emergency flatten with sellable positions, or restart reconciliation
after a live exchange fill. Treat this run as decision-useful paper evidence,
not live-readiness evidence.

## Evidence

Runtime artifacts on the Unraid host:

- Monitor JSONL:
  `/mnt/user/appdata/krakked/state/paper-validation-monitor-20260620-171130.jsonl`
- Monitor log:
  `/mnt/user/appdata/krakked/state/paper-validation-monitor-20260620-171130.log`
- Monitor PID file:
  `/mnt/user/appdata/krakked/state/paper-validation-monitor-20260620-171130.pid`
- Pre-upgrade environment backup:
  `/mnt/user/code/krakked/.env.pre-rc8-20260620-170459.bak`

The paper session was stopped intentionally after the monitor window to freeze
the evidence for review.

## Setup And Investigation Log

- The existing pinned image, `v0.1.1-rc.7`, predated the PR 840
  account-truth gate corrections.
- A new release candidate tag, `v0.1.1-rc.8`, was created from
  `main` commit `8b6bd577901a3ff8bf50f9766a546193f9bb32ed`.
- The tag-driven release workflow completed and published the GHCR image.
- The Unraid install was repinned to `v0.1.1-rc.8` with expected SHA
  `8b6bd577901a3ff8bf50f9766a546193f9bb32ed`.
- The app reported the expected image tag and build SHA throughout the monitor
  window.

Host setup exposed a non-Krakked appliance wrinkle:

- The Unraid host had rebooted with the array stopped, `/mnt/user` absent, and
  Docker unavailable.
- Array autostart was disabled.
- `emcmd cmdStart=Start` did not start the array and appeared to trigger an
  `emhttpd` segfault.
- Restarting the management daemon with `emhttp start` and then calling
  `emcmd "startState=STOPPED&cmdStart=Start"` started the array.
- Docker came back up, Krakked autostarted, and the container was then recreated
  on `v0.1.1-rc.8`.

Finding: the app validation recovered, but the appliance path still has host
startup fragility outside the Krakked process.

## Monitor Summary

- Samples: 150.
- Sample interval: roughly 1 minute.
- Sample range: `2026-06-21T00:11:30Z` through
  `2026-06-21T02:40:44Z`.
- Local time range: about 2026-06-20 17:11 through 19:40 PDT.
- Session active samples: 150 of 150.
- Lifecycle: active in all samples.
- Profile: `paper-soak-2026-06-18` in all samples.
- Mode: paper in all samples.
- Image tag: `v0.1.1-rc.8` in all samples.
- Build SHA: `8b6bd577901a3ff8bf50f9766a546193f9bb32ed` in all samples.
- Deployment drift samples: 0.
- Endpoint errors in monitor JSONL: 0.
- Monitor log errors: none observed.

## Market Data

- Market status was streaming in 133 samples.
- Market status was degraded in 16 samples.
- Market status was unavailable in 1 sample.
- The degraded reason was `data_stale`.
- The recurring stale pair was `ADA/USD`.
- Streaming health usually showed 4 pairs streaming and 0 stale pairs.
- Degraded samples usually showed 3 pairs streaming and 1 stale pair.

Finding: market health was mostly good, and the app kept operating on the
healthy streams, but `ADA/USD` staleness remains real operator noise. Live
readiness correctly treated degraded market data as a blocker when it appeared.

## Strategy Diagnostics

The active starter strategies were `trend_core` and `majors_mean_rev`.

Observed latest-summary statuses:

- `trend_core`: 147 samples deferred for no new closed bar; 3 samples emitted
  intents.
- `majors_mean_rev`: 147 samples deferred for no new closed bar; 3 samples
  evaluated with no signal.

`majors_mean_rev` no-signal summaries reported `not_below_lower_band`, which is
clear and strategy-specific enough to explain why it stayed quiet.

`trend_core` emitted intents in three sampled plans:

- At the first sampled plan it generated four intents across `BTC/USD`,
  `ETH/USD`, `SOL/USD`, and `ADA/USD`.
- At two later plans it generated three intents, including `BTC/USD`,
  `ETH/USD`, and `SOL/USD`.
- Top-level no-signal reasons stayed empty when the strategy emitted intents,
  while per-context details remained available.

Finding: the strategy-silence diagnostics are now doing their job. Closed-bar
deferrals, no-signal decisions, and intent-emitting cycles were distinguishable
in the sampled runtime state.

## Execution, OMS, And Portfolio Evidence

The monitor observed three execution plans:

- `plan_1782000633`
  - Time: `2026-06-21T00:10:41Z`.
  - Result: one filled dry-run `BTC/USD` buy order.
  - Local order ID: `d8c987ef-5396-53b6-8781-45bd7e91fb2b`.
  - Volume: `0.00778963`.
  - Average fill price: `64508.8`.
  - Kraken order ID: `dry-d8c987ef-5396-53b6-8781-45bd7e91fb2b`.
- `plan_1782003714`
  - Time: `2026-06-21T01:01:56Z`.
  - Result: no orders, no warnings, no errors.
- `plan_1782007298`
  - Time: `2026-06-21T02:01:41Z`.
  - Result: two filled dry-run orders.
  - `BTC/USD` sell order `070404b1-69a4-5a05-b095-a5c69e3d2145`,
    volume `0.00062927`, average fill price `63837.3`.
  - `ETH/USD` buy order `fb9147ff-cc4d-56ac-bf90-1c10e8d7da8b`,
    volume `0.02333862`, average fill price `1742.67`.

Latest sampled execution state:

- Open orders: none.
- Paper trades: 3.
- Portfolio equity: `9996.995343387502`.
- Portfolio cash: `9496.999701111601`.
- Realized PnL: `-0.42255480500000003`.
- Unrealized PnL: `-2.5820984760000485`.
- Baseline source: `paper_wallet`.
- Exchange reference equity: `12.3006622445`.
- Exchange reference cash: `0.0091`.

Latest sampled positions:

- `BTC` / `XBT`: base `0.00716035`, average entry `64508.8`,
  current price `64175.65`, value `459.5201154775`.
- `ETH`: base `0.02333862`, average entry `1742.67`,
  current price `1734.245`, value `40.4748850419`.

Finding: this run closes the biggest gap from the first soak. The paper session
did not merely stay alive; it produced strategy intents, OMS records, dry-run
fills, trades, and portfolio positions.

## Live Readiness And Account Truth

Live readiness was blocked in all samples, as expected in paper mode. Blocker
counts:

- `live_gates`: 127 samples.
- `live_gates,market_data`: 17 samples.
- `live_gates,portfolio_sync`: 6 samples.

The six `portfolio_sync` blocker samples were all paper-mode samples where
system health reported `portfolio_sync_ok=false` with no reason, while risk
status still reported `portfolio_sync_ok=true`. The sampled timestamps were:

- `2026-06-21T00:25:31Z`
- `2026-06-21T00:57:34Z`
- `2026-06-21T01:12:36Z`
- `2026-06-21T01:29:37Z`
- `2026-06-21T01:46:39Z`
- `2026-06-21T02:01:41Z`

Code review of the current path explains the mismatch:

- `PortfolioService.sync()` sets `last_sync_ok=False` and
  `last_sync_reason=None` at sync start.
- Paper sync later restores `last_sync_ok=True`, clears the reason, and updates
  `last_sync_at`.
- `/api/system/health` samples `read_portfolio_sync_status()` directly from the
  portfolio object. In non-live mode that helper preserves the raw status.
- `StrategyEngine.get_risk_status()` returns cached risk status, so it can still
  show the previous healthy state during a very short in-progress sync window.

Finding: this paper-mode blip was not evidence of a submitted live order bypass,
but it did expose a broader account-truth consistency issue with live
implications. Health, readiness, risk status, and OMS checks should read one
atomic account-truth snapshot so an in-progress sync is visible as verification
work, not a reasonless degraded state.

## Docker Health

The app HTTP health endpoint was reachable, and monitor endpoint reads
succeeded. Docker nevertheless reported the container as unhealthy after the
run. The current Docker health failure was:

```text
OCI runtime exec failed: open /run/user/0/runc-process...: no such file or directory: unknown
```

The compose healthcheck uses an in-container command to call the app health
endpoint:

```text
python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=5).read()"
```

Finding: the failure appears to be Docker/Unraid `exec` plumbing, not an app
health endpoint failure. Any Docker healthcheck that depends on starting an
in-container process can be noisy under this host failure mode. The operator
surface should treat app-level health probes as more authoritative than Docker
health on this Unraid setup, or the deployment docs should explain the
difference plainly.

## What This Run Proved

- Pinned-image startup on `v0.1.1-rc.8`.
- Runtime provenance stayed stable with no image/SHA drift.
- The selected paper session stayed active through the monitor window.
- Market data was mostly streaming, with visible degraded windows.
- Strategy diagnostics made deferred/no-signal/intent cycles legible.
- Strategy intents reached execution planning.
- OMS produced dry-run filled orders in paper mode.
- Paper trades and portfolio positions updated.
- Live readiness stayed blocked in paper mode.

## What This Run Did Not Prove

- Live Kraken AddOrder submission.
- Real exchange fills.
- Real live trade/ledger ingestion.
- Restart reconciliation after exchange-side fill.
- Emergency flatten with sellable paper positions or open orders.
- Manual backup/export/restore after PR 840.
- Docker health reliability on Unraid.
- Any strategy edge, profitability, or live-capital readiness.

## Recommendations

Follow-up status:

- The operator-truth cleanup landed after this validation pass.
- The deterministic seeded emergency-flatten drill now proves the API and
  background resume paths for cancel-first behavior, remaining open-order
  residue, degraded account-truth refusal, verified closeout, persisted
  execution state, and dust/no-retry behavior.

Cleanup completed after this report:

1. Account-truth snapshot consistency now keeps in-progress sync, last completed
   sync state, and drift are reported consistently across health, readiness,
   risk status, and OMS gates.
2. Unraid proof output now treats app HTTP health as authoritative when Docker
   exec health is noisy.
3. Recurring `ADA/USD` staleness is legible under the policy that enabled/open
   position pairs block while disabled/watchlist/global pairs warn.
4. Dated paper-validation profile suggestions and active DB path affordances
   make the next run easier to name, back up, and export.

Recommended next proof:

1. Run a decision-useful paper soak on a supported window/pair set so strategy
   intents, OMS rows, paper fills, portfolio sync, and the proved safety gates
   are exercised together.
2. Re-run a short paper validation only after future health/readiness/control
   surface changes.

Do not tune strategies or start a tiny live smoke from this evidence alone. The
paper loop is finally producing useful records, and the next step is to exercise
those records in a longer decision-useful soak under the now-hardened operator
and money-safety surfaces.
