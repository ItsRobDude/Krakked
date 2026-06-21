# Paper Soak Report: 2026-06-19

Date: 2026-06-19
Host: Tower / Unraid
Image: `ghcr.io/itsrobdude/krakked:v0.1.1-rc.5`
Build SHA: `36de0019c46ded382896c4a0bc08058d4fc8350c`
Profile: `paper-soak-2026-06-18`
Mode: paper
ML: disabled

## Verdict

The first pinned-image overnight paper soak proved that the appliance can stay
up, keep the selected paper profile active, preserve runtime provenance, sample
health continuously, stop/start cleanly, restart without image drift, and
export/restore a profile-isolated paper database.

It did not prove a production-grade normal trading loop. The active strategies
were alive, but most strategy contexts were waiting for a new closed strategy
bar and the fresh evaluations still produced no intents. The session produced
no actions, risk blocks, OMS orders, execution results, trades, or ledger
entries. Treat this run as operational lifecycle evidence, not decision,
execution, or money-safety evidence.

## Evidence

Runtime artifacts on the Unraid host:

- Monitor JSONL:
  `/mnt/user/appdata/krakked/state/paper-soak-monitor-20260618-165715.jsonl`
- Pre-drill monitor copy:
  `/mnt/user/appdata/krakked/state/paper-soak-monitor-20260618-165715-pre-drill-20260619-065036.jsonl`
- Morning report:
  `/mnt/user/appdata/krakked/state/paper-soak-report-20260619-0656.md`
- Pre-drill DB backup:
  `/krakked/state/paper-soak-2026-06-18/portfolio.db.202606191350.bak`
- Pre-drill export:
  `/krakked/state/krakked-soak-pre-drill-20260619-065108.zip`
- Scratch restore directory:
  `/krakked/state/restore-check-soak-20260619-065451`

Use the pre-drill monitor copy for clean overnight evidence. The primary monitor
continued running after the manual control drills, so later samples include
intentional stop/start and restart behavior.

## Setup

- Deployed pinned image `v0.1.1-rc.5` with SHA
  `36de0019c46ded382896c4a0bc08058d4fc8350c`.
- Used a clean paper profile named `paper-soak-2026-06-18`.
- Used an isolated paper database at
  `/krakked/state/paper-soak-2026-06-18/portfolio.db`.
- Kept `execution.mode=paper`, `execution.validate_only=false`, and
  `execution.allow_live_trading=false`.
- Disabled ML.
- Enabled starter strategies `trend_core` and `majors_mean_rev`.
- Backfilled `1h`, `4h`, and `1d` OHLC; subscribed to `1m` WebSocket data.

Pre-soak replay readiness was green for the configured pairs/timeframes, and the
one-command replay completed and published a latest report. That replay was a
setup confidence check, not a substitute for the live paper-session evidence in
this document.

## Overnight Monitor Summary

- Samples before drills: 167.
- Sample range: `2026-06-18T23:57:15Z` to
  `2026-06-19T13:47:21Z`.
- Missing samples: 0.
- Session active in all samples: yes.
- Lifecycle in all samples: active.
- Profile in all samples: `paper-soak-2026-06-18`.
- Mode in all samples: paper.
- ML enabled in samples: false.
- Deployment drift: false in all samples.
- Section errors: none observed.

The monitor established that the runtime stayed up and inspectable through the
overnight window.

## Market Data

- Market status was streaming in 152 samples.
- Market status was degraded in 15 samples.
- Degraded reason was `data_stale`.
- The cockpit stale pair in degraded samples was `ADA/USD`.
- Parsed logs contained 307 market-data warnings during the sampled window.
- Stale-pair warning counts were 302 for `ADAUSD` and 3 for `ETHUSD`.
- The latest pre-drill sample was streaming with 4 pairs streaming and 0 stale
  pairs.

Finding: top-level market health mostly recovered, but repeated `ADA/USD` stale
windows are real operator noise and may be part of why active strategy contexts
were not useful.

## Strategy, Risk, And OMS Evidence

Database counts before the control drills:

- `execution_plans`: 785.
- `execution_orders`: 0.
- `execution_order_events`: 0.
- `execution_results`: 0.
- `trades`: 0.
- `ledger_entries`: 0.
- `snapshots`: 14.
- `balance_snapshots`: 1.

After the emergency-flatten drill, `execution_plans` increased to 788 and
`snapshots` increased to 15. Order, execution-result, trade, and ledger counts
remained zero.

All observed execution plans had `action_count=0` and `blocked_actions=0`.
Strategy metadata used the old `skipped_stale_timeframe_contexts` field, but a
deeper review showed that this mostly meant "no new closed bar since the last
evaluation," not necessarily stale market data:

- `majors_mean_rev`: 771 skipped stale timeframe contexts out of 785 contexts.
- `trend_core`: 1551 skipped stale timeframe contexts out of 1570 contexts.

Fresh evaluations did happen after scheduled OHLC tail refreshes, but they still
produced no intents: `trend_core` was blocked by trend conditions, and
`majors_mean_rev` saw at least one below-band setup blocked by the regime gate.

Finding: the strategy loop ran, but the session was mostly
alive-without-useful-decisions. This is the highest-priority product/runtime
finding from the soak. The next paper run should either produce meaningful
strategy/risk/OMS evidence or clearly explain, in the operator view, whether a
strategy is waiting for the next closed bar, missing data, seeing stale data, or
choosing no trade because its signal rules are not met.

## Live Readiness

- Live readiness was blocked in all samples, as expected for paper mode.
- The `live_gates` blocker was present in all samples.
- A `portfolio_sync` blocker appeared in 5 samples.
- A `recent_activity` warning appeared in all samples because no execution or
  risk-decision proof appeared.
- A `market_data` warning appeared in the 15 degraded market-data samples.

Finding: live readiness is correctly not green in paper mode, but the persistent
`recent_activity` warning is useful only if the operator can tell whether the
absence of activity is a deliberate no-trade decision, closed-bar waiting, a
stale-data skip, missing data, or a strategy misconfiguration.

## Docker Health

The application HTTP routes stayed reachable and a manual health probe inside
the container returned HTTP 200. Docker health nevertheless became unhealthy
overnight with repeated OCI exec failures:

```text
OCI runtime exec failed: open /run/user/0/runc-process... no such file or directory
```

Docker health later recovered to healthy before or during the restart drill, and
it was healthy again after the restart.

Finding: Docker's healthcheck signal was noisy on this Unraid run. The app was
observable and responsive while Docker reported unhealthy. The Unraid deployment
needs either a more robust healthcheck or clearer operator guidance about which
health signal is authoritative.

## Control Drills

### Stop/Start As Pause/Resume

Result: pass.

- `POST /api/system/session/stop` moved the session from active to inactive and
  lifecycle from active to ready.
- `POST /api/system/session/start` moved the session back to active and
  lifecycle active.
- Profile and mode were retained.
- Market and portfolio health were good after resume.

UX finding: the operator concept is pause/resume, but the current API/product
surface is stop/start. The UI should either adopt pause/resume language or make
the stop/start semantics plain.

### Container Restart

Result: pass with expected limitation.

- The container restarted without image or build-SHA drift.
- The API returned after roughly 15 seconds.
- The selected profile, mode, config, and paper wallet persisted.
- The session came back inactive/ready, not active. That matches current code:
  active session state is memory-only and does not auto-resume after restart.
- Manual session start restored the active paper session.
- Market data returned to streaming after active start.

Operator finding: restart behavior is safe, but it must be explicit that a
restart does not automatically resume an active trading session.

### Backup, Export, And Restore

Result: pass after using the explicit soak DB path.

- DB backup succeeded for
  `/krakked/state/paper-soak-2026-06-18/portfolio.db`.
- An initial export without `--db-path` failed correctly because the default
  `/app/portfolio.db` does not exist for this isolated profile.
- Export succeeded with explicit `--config-dir` and `--db-path`.
- Import into scratch paths succeeded.
- Scratch DB integrity check returned `ok`.

Operator finding: profile-isolated DB paths make the default export path easy to
get wrong. Backup/export should become profile-aware, or the operator UI should
show the exact active DB path and use it by default.

### Emergency Flatten

Result: pass, but limited.

- The flatten endpoint accepted the required confirmation phrase.
- It returned success with no orders.
- It warned that there were no sellable positions and no dust or untradeable
  holdings.
- Session remained active.
- `emergency_flatten` remained false.
- DB order, result, trade, and ledger counts remained zero.

Proof limitation: this only proves the no-position flatten path. It does not
prove close-out behavior with positions, open orders, partial fills, stale
portfolio sync, or restart retry.

## What This Run Proves

- Pinned-image paper runtime can stay active overnight.
- Runtime provenance stayed stable and visible.
- The monitor captured health samples without gaps.
- Stop/start returns the session to a healthy active state.
- Container restart preserves profile/config/wallet and avoids image drift.
- Backup/export/restore works when pointed at the profile-isolated DB.
- Emergency flatten's empty-portfolio path is safe and non-destructive.

## What This Run Does Not Prove

- That enabled strategies can make useful live paper decisions over the active
  data feeds.
- That risk blocks or clamps occur correctly during a normal paper session.
- That OMS submit/record/reconcile behavior works during a normal paper session.
- That emergency flatten closes real positions or survives restart/retry cases.
- That Docker health is a dependable operator-facing signal on Unraid.
- That the product is ready for live order submission.

## Follow-Up Work

Priority order from this soak:

1. Distinguish closed-bar deferrals from true market staleness and surface a
   clear "why no strategy acted" explanation for each enabled strategy.
2. Make Docker healthcheck behavior reliable on Unraid, or route operators to a
   better app-level health signal.
3. Clarify pause/resume versus stop/start language in the operator UI.
4. Make restart behavior explicit: profile persists, active-running intent does
   not auto-resume.
5. Make backup/export profile-aware so isolated paper DBs do not require manual
   path knowledge.
6. Build a deterministic paper flatten drill with seeded synthetic positions,
   open orders, partial fills, restart, and reconciliation checks.
7. Turn this manual monitor parsing into a repeatable `krakked` report command
   or scripted runbook.

## Follow-Up Status

Status as of 2026-06-20:

- Strategy diagnostics now distinguish closed-bar deferrals, no data, stale
  data, strategy errors, no-signal reasons, and emitted intents for the enabled
  starter strategies.
- Starter strategy parameters are explicit in the active paper profile/config,
  so future paper runs do not depend on constructor defaults for proof context.
- The fake Kraken harness now proves one narrow coherent order lifecycle:
  AddOrder, OpenOrders, ClosedOrders, Balance, TradesHistory, Ledgers, full
  fill, partial fill, restart reconciliation, degraded/stale private reads, and
  the trade-ledger `refid` to TradesHistory ID assumption used by the
  account-truth gate.
- Live Balance/TradesHistory/Ledgers unavailability, never-synced live cold
  start, stale sync age, missing trade-history evidence, and material drift now
  keep account truth degraded and block live opening risk.

Still open from this soak:

- Docker healthcheck behavior on Unraid.
- Pause/resume versus stop/start language.
- Restart no-auto-resume wording.
- Profile-aware backup/export.
- Seeded emergency flatten with positions and open orders.
- Short paper validation of the new diagnostics/account-truth surfaces before
  any live-capital readiness claim.
