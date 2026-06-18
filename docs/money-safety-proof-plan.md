# Money Safety Proof Plan

Date: 2026-06-17

## Purpose

This document defines what it would mean for Krakked to become a bot an
operator could reasonably trust with real money.

Trusting money with Krakked does not mean:

- guaranteed profitability;
- an autonomous market-beating strategy;
- permission to scale capital after one successful smoke test;
- permission to loosen replay/data checks to make strategy evidence look better.

Trusting money with Krakked means:

- the bot is hard to make dangerous by accident;
- live order submission has explicit, tested, fail-closed gates;
- exchange state, local state, and operator state are reconciled and visible;
- order lifecycle recovery is proven across restarts and partial failures;
- every order can be explained after the fact;
- strategy claims stay honest and separate from safety claims.

This is a proof plan, not a feature wish list. A feature is not considered
money-safe because the code path exists. It is considered money-safe only after
the repo contains a deterministic proof that the behavior binds under failure.

## Current Status Scorecard

Current trust level: Level 0, research and paper only.

- Milestone A, fake Kraken and fault harness: started. An initial
  order-lifecycle fake exists for OMS/adapter/SQLite proofs; account balances,
  trades, ledgers, partial fills, and stale-read modeling remain missing.
- Milestones B-E, order lifecycle, reconciliation, risk limits, and emergency
  operations: building blocks exist. Milestone B now has an initial passing
  proof for AddOrder response-loss duplicate-submit prevention plus manual
  `submit_unknown` recovery tooling, but broader crash/restart, fill, balance,
  reconciliation, and emergency-operation proofs remain incomplete.
- Milestone F, data continuity: continuity tooling exists, but strict replay
  gates do not yet consume continuity gaps directly.
- Milestone G, paper soak, validate-only drill, and tiny live smoke: not
  started.

Known live blockers:

- fake Kraken/fault harness is still order-lifecycle-only and does not yet model
  full account state, fills, trades, ledgers, or stale reads;
- crash-safe submit-intent behavior is only proven for the initial AddOrder
  response-loss scenario; broader process-death and reconciliation drills remain
  incomplete;
- live balance fetch failure can still fall back to local ledger state in some
  paths;
- out-of-band alerting is only proved for `submit_unknown` and blocked opening
  risk; broader fail-closed alert scenarios remain unproved;
- no quantified live reconciliation staleness policy or relative drift
  threshold;
- no enforced live strategy boundary that prevents research strategies from
  submitting live orders.

## Current State

Krakked has substantial safety and observability building blocks:

- gated live execution through `execution.mode`, `execution.validate_only`, and
  `execution.allow_live_trading`;
- OMS persistence for execution plans, local orders, order events, and execution
  results;
- Kraken order tagging via `userref` and live per-order `cl_ord_id`;
- exchange order refresh/reconciliation hooks for open and closed orders;
- portfolio sync from trades, ledgers, and balances;
- drift detection and kill-switch wiring;
- emergency cancel-all and flatten-all paths;
- dead-man switch heartbeat support;
- cockpit visibility for health, portfolio, risk, strategies, replay evidence,
  market data, decision trace, and live readiness;
- pinned-image deployment, backup, restore, rollback, and reboot proof.

Those pieces matter. They are also not enough.

The gap is proof under failure. The remaining question is not "does a safety
path exist?" but "can we demonstrate it fails closed when the exchange, process,
network, data, or operator state is awkward?"

The strategy state must also stay blunt:

- bundled strategies are research-stage or unproven;
- recent ML evidence did not clear the EWMA benchmark and should not influence
  trading or display risk;
- strict `4h` and `1d` OHLC coverage is usable for the starter majors, but the
  default `1h` scoreboard path is still blocked by the April/May 2026 gap until
  deeper history is imported;
- strategy edge and money safety are different gates.

## Product Boundary

The realistic v1 product boundary is deliberately narrow:

- spot trading only;
- Kraken only;
- no margin, futures, options, leverage, shorting, staking automation, market
  making, high-frequency trading, or cross-exchange routing;
- no autonomous alpha claim for bundled strategies;
- no capital scaling before repeated operational proof;
- no live behavior that depends on relaxed replay/data gates;
- no live Kraken key with withdrawal, deposit, or funding-movement permissions.

Live Kraken API keys must be minimum-permission keys. For a live trading key,
that means read/reconcile and trade permissions only: query funds, query open
and closed orders/trades, query ledger entries, create/modify orders, and
cancel/close orders. Authenticated WebSocket permission is allowed only if the
runtime actually needs private WebSocket subscriptions. Withdrawal, deposit,
export, Earn, and broad institutional permissions are not allowed for the
Krakked live trading key.

Prefer separate keys per environment or purpose, IP whitelisting, and key
expiration where practical. The key boundary is a money-safety invariant: a
software gate can limit bad trades, but it cannot make a withdrawal-capable key
safe after compromise.

The likely trustworthy product shape is one of these:

1. Human-guided execution and risk discipline.
   The operator brings intent. Krakked enforces limits, executes, reconciles,
   records decisions, and makes state visible.

2. Mechanical DCA or rebalancing.
   Krakked performs boring, explicit, low-frequency rules without claiming
   predictive edge.

These two product shapes are the only live-money paths before separate strategy
promotion proof exists.

Alpha research remains allowed, but it is a separate research lane. It must not
become a prerequisite for shipping the safety product, and it must not bypass
the safety proof gates. Research strategies such as `trend_core`, rotation,
breakout, mean-reversion, or ML candidates must not drive live orders merely
because a config toggle enables them.

## Trust Levels

Use these levels to avoid vague "live-ready" language.

### Level 0: Research And Paper Only

The system can run paper mode, replay strategies, and surface operator state.
This is the current broad posture.

Allowed:

- paper trading;
- replay/backtest research;
- data import and continuity checks;
- UI/operator workflow hardening;
- deployment and backup drills;
- live read-only account inspection when credentials are configured.

Not allowed:

- real order submission;
- live capital scaling;
- strategy profitability claims.

### Level 1: Safety Harness Exists

The repo has a deterministic fake Kraken/fault harness that can exercise the
private exchange-facing seams.

Required harness behavior:

- `AddOrder`;
- `OpenOrders`;
- `ClosedOrders`;
- `TradesHistory`;
- `Ledgers`;
- `Balance`;
- cancel single order;
- cancel all orders;
- validate-only order calls;
- accepted orders;
- rejected orders;
- partial fills;
- full fills;
- canceled orders;
- stale open orders;
- duplicate or delayed exchange responses;
- rate-limit and service-unavailable errors;
- network timeout before response;
- network timeout after remote acceptance;
- process restart with persisted local state.

Level 1 does not prove Krakked is safe. It proves the repo has the machinery to
test safety honestly.

### Level 2: Paper Safety Proofs Pass

Deterministic tests prove local safety behavior without real order submission.

Required proofs:

- new risk is blocked when the kill switch is active;
- reduce-only emergency actions can still execute when appropriate;
- max exposure, max pair notional, max total notional, max open positions, and
  per-strategy caps bind under adversarial inputs;
- stale or missing price data fails closed for live-equivalent order paths;
- drift detection can activate the kill switch and block later new risk;
- emergency flatten persists intent and keeps retrying safely after restart;
- crash/restart recovery neither loses local open orders nor invents fills;
- audit records are sufficient to explain why every order was placed, rejected,
  blocked, canceled, or filled.

Level 2 permits stronger paper confidence. It still does not permit normal live
trading.

### Level 3: Live Read-Only And Validate-Only Proof

Krakked is connected to a real Kraken account but must not submit real orders.

Required proofs:

- credentials validate without enabling live submission;
- the live key has no withdrawal, deposit, export, Earn, or funding-movement
  permissions;
- key permissions are verified programmatically only if Kraken exposes reliable
  permission introspection for the configured key; otherwise the operator must
  provide setup evidence and a dated attestation before any Level 3 run;
- the key can perform the specific read/validate-only actions Krakked needs:
  balances, open orders, closed orders, trades, ledgers, pair metadata,
  validate-only add order, and cancel/close behavior where appropriate;
- live read-only balance/trade/ledger fetch works;
- exchange balances reconcile against the local ledger within tolerance;
- failed or stale portfolio sync blocks live readiness before any real order
  path;
- validate-only order calls use realistic pair metadata, order sizing,
  rounding, slippage, userref, and notional checks;
- live readiness reports exact blockers;
- the operator can move back to paper-only safety without ambiguity.

Level 3 proves exchange contact and validation behavior. It still does not prove
real execution behavior.

### Level 4: Tiny Live Smoke Eligible

A single tiny, supervised live smoke is allowed only after Levels 1-3 pass.

Required constraints:

- one account;
- one profile;
- one or two highly liquid spot pairs;
- hard max pair notional;
- hard max total notional;
- hard max concurrent orders;
- default order type and slippage chosen deliberately;
- written stop condition before start;
- known manual cancel-all path;
- known emergency flatten path;
- pre-run backup/export;
- live readiness green except explicitly accepted warnings;
- operator present for the full run;
- post-run reconciliation report.

The smoke test goal is plumbing proof, not profit.

### Level 5: Constrained Live Utility

Krakked may run low-capital live utility workflows only after repeated Level 4
passes.

Allowed:

- human-guided execution with explicit operator intent;
- mechanical DCA or rebalancing with conservative caps;
- continued live read-only and validate-only checks;
- small supervised increases only after clean reconciliation reports;
- active out-of-band alerts for fail-closed events.

Not allowed:

- unbounded autonomous strategy operation;
- meaningful capital allocation based on one successful live test;
- strategy promotion without strict, cost-aware, continuity-proven evidence.

Level 5 requires the bot to stop or block and tell the operator loudly. A
semi-unattended live utility is not trustworthy if kill-switch fires, drift
blocks, reconciliation failures, dead-man failures, emergency flatten failures,
or unexpected live-session stops only appear in logs or the cockpit.

## Proof Milestones

### Milestone A: Fake Kraken And Fault Harness

Why it matters:

Safety proofs need a deterministic exchange model. Without it, tests either
mock too little or depend on live Kraken behavior that is slow, flaky, and
unsafe for adversarial cases.

Current building blocks:

- `KrakenRESTClient` centralizes private endpoint calls;
- execution, portfolio, and UI tests already use fake clients and services in
  narrow places;
- OMS and portfolio services can receive injected clients/stores;
- `tests/fakes/fake_kraken.py` now provides an initial deterministic fake for
  AddOrder, OpenOrders, ClosedOrders, cancellation, and selected submit faults;
- `tests/test_money_safety_order_lifecycle.py` drives real OMS, real execution
  adapter, and real SQLite store through that fake.

Remaining proof gap:

- the current fake is enough for initial order-lifecycle proofs, but it does not
  yet model account balances, trades, ledgers, partial fills, stale reads, or
  portfolio reconciliation behavior together;
- strict failing tests currently prove the duplicate-submit and restart recovery
  gaps rather than proving the final safe behavior.

Done when:

- a fake Kraken client/harness exists in tests or test utilities;
- the harness can run at least one end-to-end order lifecycle through OMS,
  portfolio sync, and reconciliation;
- the harness can inject deterministic failures before and after remote order
  acceptance;
- new safety tests use the harness instead of one-off mocks when lifecycle
  behavior matters.

### Milestone B: Order Lifecycle And Crash/Restart Proof

Why it matters:

The highest-risk live failure is an order being accepted remotely while the
local process crashes, times out, or loses the response. The bot must not
double-submit blindly, and it must not forget remote exposure.

Current building blocks:

- local order IDs are deterministic for plan actions;
- live, non-validate AddOrder payloads use the deterministic local order ID as
  Kraken `cl_ord_id`;
- SQLite stores `cl_ord_id` in an indexed `client_order_id` column for exact
  local lookup;
- a shared tri-state attribution helper classifies lookups as `none`, `exact`
  (one candidate echoing the expected `cl_ord_id`), `unverified` (one candidate
  missing/mismatched on `cl_ord_id`), or `ambiguous` (>1 candidate); adoption
  is allowed only on `exact`, and `unverified` is kept distinct from `none`;
- admin tooling can reconcile submit intents (adopt only on `exact`), clear to
  `submit_absent` only when both endpoints are `none`, run a validate-only
  `cl_ord_id` probe, and perform explicit, audited operator force-link /
  force-clear recovery for `unverified`/`ambiguous` exchange state;
- `submit_unknown` and blocked-opening events can emit a configured webhook
  alert;
- `userref` is deterministic and persisted;
- execution orders and results are saved to SQLite;
- bootstrap loads persisted open orders and refreshes/reconciles order state;
- tests now cover remote-accepted/local-response-lost duplicate-submit
  prevention, restart recovery, generic submit uncertainty, and known
  no-accept retry boundaries.

Proof gap:

- `userref` helps attribution and recovery, but it is not full idempotency;
- the initial proof covers AddOrder response loss and service-unavailable
  ambiguity, but not every process-death point, fill timing, cancel timing, or
  stale exchange-read case;
- submit-intent state is persisted in the existing execution order table, and
  manual confirmed-absent clearing exists, but broader lifecycle drills still
  need to prove it across additional failure points;
- the validate-only `cl_ord_id` probe proves Kraken *parameter* acceptance only;
  it does not prove a live order is queryable by `cl_ord_id` or that Kraken
  echoes it back in the order payload. Auto-recovery requires an exact echoed
  `cl_ord_id`; missing/mismatched echo is treated as `unverified` and fails
  closed. Whether Kraken echoes `cl_ord_id` in OpenOrders/ClosedOrders payloads
  is unproven until a Level-4 tiny-live round trip confirms it; if it does not
  echo, auto-recovery is effectively manual-only via the audited force path.

Done when:

- an order intent is persisted before any live submission attempt;
- restart recovery inspects persisted intents and exchange open/closed orders
  before generating new risk;
- tests prove timeout-before-acceptance and timeout-after-acceptance behave
  differently and safely;
- tests prove restart does not double-submit the same intent;
- tests prove unknown in-flight state blocks new risk until reconciled.

### Milestone C: Reconciliation As A Hard Live Gate

Why it matters:

Live trading on stale or mismatched account state is how small errors become
large ones. Reconciliation must be a live permission gate, not just a warning.

Current building blocks:

- portfolio sync imports trades and ledgers, then compares local balances to
  Kraken balances;
- drift detection exists;
- risk can activate a kill switch on drift;
- live readiness can report portfolio sync and drift state.

Proof gap:

- failed balance fetch currently falls back to local ledger state in some paths;
  this is a known live-dangerous defect, not just a missing proof;
- live readiness is mostly operator-facing and not always an execution gate;
- stale sync age and last successful reconciliation need explicit live policy;
- relative drift tolerance needs explicit live policy alongside the existing
  absolute `portfolio.reconciliation_tolerance` setting;
- the exact blocking behavior needs end-to-end tests.

Initial live reconciliation policy to implement and prove:

- absolute tolerance is `portfolio.reconciliation_tolerance`, currently
  defaulting to `$1.00`;
- relative tolerance is `0.10%` of total equity;
- valued drift blocks new live risk when the mismatch exceeds
  `max(absolute_tolerance, relative_tolerance)`;
- the last successful live reconciliation must be no older than
  `min(max(2 * effective_portfolio_sync_interval_seconds, 120), 600)` seconds;
- with the current effective 300-second portfolio-loop fallback, that staleness
  limit is 600 seconds;
- balance fetch failure, unknown reconciliation status, stale reconciliation,
  or unpriced material mismatch blocks new opening risk while preserving cancel
  and reduce-only emergency paths.

Done when:

- live start is blocked if portfolio sync is missing, stale, failed, or drifting;
- new live risk is blocked if reconciliation is unknown, stale, failed, or
  drifting;
- reduce-only/cancel paths remain available during reconciliation failure;
- cockpit and live readiness name the exact reconciliation blocker;
- tests prove drift and stale sync block new risk before order submission.

### Milestone D: Risk Limits Bind Under Attack

Why it matters:

Risk limits are only useful if they bind when strategies, prices, snapshots, or
operator settings produce awkward inputs.

Current building blocks:

- risk engine enforces daily drawdown, drift kill switch, open positions,
  per-asset, per-strategy, and portfolio exposure limits;
- OMS also enforces execution guardrails such as max pair notional, max total
  notional, min notional, max concurrent orders, stale plan age, and missing
  price behavior;
- tests already cover several kill-switch and risk-engine branches.

Proof gap:

- tests are broad but not organized as a money-safety proof suite;
- live-equivalent adversarial combinations need explicit coverage;
- auto-flatten policy should not be assumed from daily drawdown alone.

Done when:

- a dedicated safety proof suite covers each live money limit;
- tests prove blocked, clamped, and reduce-only outcomes separately;
- tests prove stale prices and stale plans fail closed for live-equivalent
  submissions;
- daily drawdown first halts new risk and alerts; any auto-flatten behavior is a
  separate explicit policy with its own proof;
- per-strategy caps are required for enabled live strategies.

### Milestone E: Emergency Operations Proof

Why it matters:

The operator needs the system to stop cleanly when something is wrong. Emergency
controls are live-money features, not UI conveniences.

Current building blocks:

- cancel single order;
- cancel all orders;
- emergency flatten route;
- emergency flatten session persistence;
- background emergency flatten loop;
- dead-man switch heartbeat support;
- admin CLI panic path.
- webhook alert transport for initial submit-unknown and blocked-opening
  fail-closed events.

Proof gap:

- emergency behavior is not proven against live-like exchange edge cases;
- flatten can be unsafe if open orders remain, sync fails, or positions are
  stale;
- dead-man heartbeat needs realistic live validation;
- only the submit-unknown and blocked-opening alert path is currently proved;
  kill-switch, drift, stale reconciliation, dead-man, emergency-flatten, and
  unexpected-stop alerts still need coverage;
- runbooks and proof outputs should be tied to tests/drills.

Done when:

- tests prove cancel-all reconciles open/closed state after success;
- tests prove flatten refuses to sell against stale positions or uncleared open
  orders;
- tests prove emergency flatten intent persists and resumes after restart;
- tests prove dust/untradeable positions are reported without infinite retry;
- tests prove dead-man heartbeat is refreshed only when live submission is
  actually allowed;
- tests prove kill-switch fire, drift block, stale or failed reconciliation,
  dead-man failure, emergency flatten failure/refusal, and unexpected live
  session stop emit an out-of-band alert;
- the alert path itself is tested, not only the local event that requests an
  alert;
- a runbook lists exact operator actions for kill switch, cancel, flatten,
  backup, alert response, and return-to-paper.

### Milestone F: Data Continuity In Strict Replay Gates

Why it matters:

Money safety and strategy evidence are separate, but evidence must still be
honest. A replay window with a middle gap must not be treated as strict-ready
just because first and last timestamps span the requested range.

Current building blocks:

- OHLC import now reports timestamp continuity;
- `krakked ohlc-continuity` can report exact gap ranges;
- replay preflight reports missing and partial series;
- docs already warn that `1h` strict scoreboards are blocked by the Q2 2026
  April/May gap for the starter majors.

Proof gap:

- backtest strict-data checks do not yet consume continuity gaps directly;
- scoreboards can still rely on first/last coverage status;
- cockpit does not yet expose data-continuity proof as an operator trust signal.

Done when:

- replay preflight includes continuity status per pair/timeframe;
- strict-data mode fails on continuity gaps inside the requested execution or
  warmup window;
- unified scoreboards mark continuity-blocked windows honestly;
- reports include exact gap ranges and missing interval counts;
- cockpit or latest replay summary surfaces data continuity status.

### Milestone G: Paper Soak, Validate-Only Drill, Tiny Live Smoke

Why it matters:

Passing tests is necessary but not sufficient. The operator also needs to see
the appliance behave boringly for real runtime sessions.

Current building blocks:

- pinned-image deployment proof passed;
- backup/export/import flows exist;
- cockpit snapshot and live readiness are available;
- normal paper mode uses a persistent synthetic wallet.

Proof gap:

- there is not yet a dated long-session report for the current safety posture;
- live validate-only has not been turned into a repeatable readiness drill;
- tiny live smoke criteria need to be written before any smoke run.

Done when:

- a long paper session report records cadence, market-data freshness, strategy
  decisions, risk blocks, OMS rows, portfolio snapshots, UI freshness,
  pause/resume, strategy changes, weight changes, restart behavior, emergency
  controls, export, and restore;
- validate-only live drill proves credentials, metadata, sizing, rounding,
  readiness, and exchange contact without real order submission;
- tiny live smoke has a pre-written stop condition, caps, pair list, account,
  backup path, operator checklist, and post-run reconciliation report.

## Non-Goals

Do not use this proof plan to justify scope creep.

Explicit non-goals:

- no margin or derivatives;
- no high-frequency execution;
- no cross-exchange routing;
- no strategy parameter wandering as a substitute for evidence;
- no model promotion based on ML-only reports;
- no loosening strict data coverage to get a green scoreboard;
- no live capital scaling after one clean smoke;
- no "paper profitable" claim unless costs, continuity, cash, and buy-hold
  baselines are all included;
- no live order submission from helper commands that are intended to be safe
  diagnostics.

## Immediate Next Slice

The initial AddOrder response-loss and submit-unknown hardening proofs are now
green. The next engineering slice should widen the same fake-exchange harness
toward reconciliation evidence without claiming full live readiness.

Recommended next behavior slice:

1. Extend the fake Kraken harness to model balances, trades, ledgers, partial
   fills, closed-order records, and stale reads for one narrow scenario.
2. Prove live balance/reconciliation failures block new opening risk while
   preserving cancel/reduce-only emergency paths.
3. Replace any live balance-fetch fallback to local ledger state with a
   fail-closed status before new opening risk can be submitted.
4. Keep the scenario small: one pair, one order, one mismatch or stale-read
   condition, one expected block.

That slice should advance Milestone C. It should not broaden into dashboard UI,
strategy promotion, extra alert scenarios, or data-continuity scoreboards until
the reconciliation gate has a deterministic failing/passing proof.

## Documentation Rules For Future Work

When adding or modifying money-safety behavior:

- update the current status scorecard whenever a trust level or milestone moves;
- state which trust level or milestone the change advances;
- include the exact failure mode being proved;
- prefer deterministic fake-exchange tests for lifecycle behavior;
- keep live-capital claims out of PR descriptions unless the proof gates have
  passed;
- do not imply live readiness unless API-key permissions, reconciliation
  thresholds, alerting, and live-strategy boundary status are stated honestly;
- update this document when a proof gate moves from "building blocks exist" to
  "proved by tests/drill";
- keep strategy edge claims in replay/research docs, not in money-safety
  readiness notes.

## Current Recommendation

Do not prioritize another strategy scoreboard as the next money-safety task.
Do not prioritize live UI polish as the next money-safety task.

Prioritize the fake Kraken/fault harness and the first crash/restart order
lifecycle proof. Once that is in place, the remaining safety proof milestones can
be implemented as meaningful tests instead of scattered mocks and optimistic
operator notes.
