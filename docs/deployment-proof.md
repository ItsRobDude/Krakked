# Deployment Proof V1 (Unraid, paper mode)

First product-pivot deliverable. The goal is to prove that Krakked actually runs
end to end as a Docker-first self-hosted product on a real Unraid host — first
boot, persistence, paper safety, operator visibility, backup/restore, and
live-gates-closed-by-default — and to capture any gaps the drill exposes.

## Current-main proof runner

The historical V1 result below proves commit `40c21ea`. For any current branch
or release candidate, run the host-side proof runner from the Krakked checkout
on Unraid:

```bash
git pull --ff-only
bash scripts/unraid_deployment_proof.sh --host-url http://<unraid-ip>:8088
```

By default the runner calls `scripts/unraid_bootstrap.sh --recreate --start`,
keeps existing appdata config, writes a dated log and summary under
`/mnt/user/appdata/krakked/state`, and exits non-zero if a required acceptance
check fails. It checks the same deployment contract as V1: first boot/recreate,
container health, persisted mounts, UI/health endpoints, forced-safe paper
`run-once`, synthetic portfolio-store evidence, live gates closed, restart
persistence, export, restore into scratch state paths, and final health.

Useful variants:

```bash
# Published-image proof once KRAKKED_IMAGE / KRAKKED_IMAGE_TAG are pinned in .env
bash scripts/unraid_deployment_proof.sh --mode image --host-url http://<unraid-ip>:8088

# Use only when the host is already recreated and you want a non-recreate check
bash scripts/unraid_deployment_proof.sh --no-recreate --host-url http://<unraid-ip>:8088
```

Paste back the `DEPLOYMENT_PROOF_RESULT`, pass/fail/warn counts, commit, runtime
provenance payloads, and log path from the summary when recording a new proof.
Release sign-off requires `fail=0`, no skip warnings, `skip_run_once=false`, and
`skip_restore=false`.

This is an operator drill you run on the Unraid box. Fill in the Results column
and the Findings section. It changes no runtime behavior by itself; it is the
acceptance test for "Krakked installs and runs cleanly for a non-developer
operator," which the [product roadmap](./product-roadmap.md) lists as the first
next milestone.

See [`unraid.md`](./unraid.md) for the low-terminal install path and
[`docker.md`](./docker.md) for the generic compose flow.

## Environment under test

- Host: Unraid server (home server).
- Install path: `bash scripts/unraid_bootstrap.sh --start` from the Krakked repo
  checkout on the host.
- Generated compose file: `compose.unraid.yaml`.
- Appdata volumes: `/mnt/user/appdata/krakked/{config,data,state}`.
- Host port: `8088` → container `8080`. UI: `http://<unraid-ip>:8088`.
- Health endpoint: `http://<unraid-ip>:8088/api/health`.
- Mode: paper (default). Live gates must stay closed for this drill.

Record the build under test:

Historical V1 result:

- Commit / image tag: `40c21ea` / `krakked:local`
- Date run: `2026-05-31 10:50:49-10:52:02 America/Los_Angeles`
- Unraid version / Docker + Compose version: Unraid `7.2.4`, Docker
  `27.5.1`, Docker Compose `v5.1.4`
- Host URL: `http://192.168.50.78:8088`
- Proof log:
  `/mnt/user/appdata/krakked/state/deployment-proof-20260531-105049.log`
- Proof summary:
  `pass=13`, `fail=0`, `warn=0`, `proof_rc=0`,
  `DEPLOYMENT_PROOF_RESULT=PASS`

## Prerequisites

- Repo checked out on the Unraid host (the bootstrap verifies the
  `ItsRobDude/krakked` remote before doing setup).
- Docker (and ideally Compose) available; the helper falls back to plain
  `docker build` / `docker run` if Compose is absent.
- A `KRAKKED_SECRET_PW` value for the encrypted secrets store. Live Kraken API
  keys are **optional** for this paper drill (paper mode uses the synthetic
  wallet and does not transmit live orders).

## Pinned-image upgrade and rollback drill

Use this after a source-mode proof passes and a GHCR image tag exists.

1. Pin `.env` to the published image, for example
   `KRAKKED_IMAGE=ghcr.io/itsrobdude/krakked` and
   `KRAKKED_IMAGE_TAG=v0.1.0`.
2. Run `bash scripts/unraid_deployment_proof.sh --mode image --host-url http://<unraid-ip>:8088`
   with no skip flags.
3. Change only `KRAKKED_IMAGE_TAG` to the next version or `sha-*` tag and rerun
   the same image-mode proof.
4. Change `KRAKKED_IMAGE_TAG` back to the prior pinned tag and rerun the same
   image-mode proof.
5. Record all three summaries and confirm the same appdata stayed mounted, the
   provenance changed as expected, and backup/restore/run-once checks all passed.

## Acceptance criteria / drill steps

Run top to bottom. Each step lists the command/check and what "pass" looks like.

| # | Step | Command / check | Pass condition | Result | Notes |
|---|------|-----------------|----------------|--------|-------|
| 1 | First boot | `bash scripts/unraid_bootstrap.sh --start` | Helper seeds appdata config, writes `compose.unraid.yaml` + `.env`, builds, and starts the container with no errors | PASS | Final proof rebuilt `krakked:local` and started the Compose service. |
| 2 | Container healthy | `docker compose -f compose.unraid.yaml ps` | `krakked` is `running` and reports `healthy` (after the ~20s start period) | PASS | Final status: `krakked-src-krakked-1` running and healthy. |
| 3 | Mounts created & populated | `ls /mnt/user/appdata/krakked/config /mnt/user/appdata/krakked/state` | `config.yaml` / `config.paper.yaml` present in config; state dir writable | PASS | Config and state folders were present; state write probe succeeded. |
| 4 | Health endpoint | `curl -fsS http://<unraid-ip>:8088/api/health` | Returns a healthy response (HTTP 200) | PASS | Returned `{"data":{"status":"ok"},"error":null}`. |
| 5 | UI reachable | Open `http://<unraid-ip>:8088` in a browser | Dashboard loads; startup/unlock state is legible (warmup is clearly distinguishable from a fault) | PASS | HTTP UI check returned the built HTML; browser check reached the operator UI after unlock. |
| 6 | Paper safety cycle | `docker compose -f compose.unraid.yaml run -T --rm krakked run-once < /dev/null` | One forced-safe paper cycle completes; no live order submission; orders/results recorded to the synthetic store | PASS | Completed after the `run-once` stub/cleanup fix. It logged two order-routing rejections, but the forced-safe paper command returned successfully and no live submission occurred. |
| 6b | Synthetic store activity evidence | Query `/krakked/state/portfolio.db` | Synthetic store exists and has decision/execution rows from paper activity | PASS | Counts included `decisions=368`, `execution_plans=39967`, `execution_orders=6`, `execution_results=184`, `snapshots=704`, `trades=6`. |
| 7 | Live gates closed by default | Inspect runtime/config (UI mode indicator and `config.paper.yaml`) | `execution.mode=paper`, `allow_live_trading=false`; UI shows paper mode | PASS | `mode: "paper"`, `validate_only: true`, `allow_live_trading: false`, `paper_tests_completed: false`. |
| 8 | Restart persistence | `docker compose -f compose.unraid.yaml down` then `up -d` | Container comes back healthy; `state/portfolio.db` and config survive the restart (state from step 6 still present) | PASS | `portfolio.db` remained present across restart; service returned healthy. |
| 9 | First backup / export | `export-install` (see command below) | Backup archive lands at `/mnt/user/appdata/krakked/state/krakked-first-backup.zip` | PASS | Archive created at `/krakked/state/krakked-first-backup.zip`, about `5.3M`. |
| 10 | Restore round-trip | `import-install` of the archive into a scratch location | Import succeeds and reports the restored config/state | PASS | Scratch restore succeeded under `/krakked/state/restore-check/*`; `--force` was used because scratch data already existed from earlier proof attempts. |
| 11 | Recreate after change | `bash scripts/unraid_bootstrap.sh --recreate --start` | Container rebuilds/recreates while keeping existing appdata config (no `--force`) | PASS | Recreate kept existing appdata config and restarted cleanly. |
| 11b | Final container status | `docker compose -f compose.unraid.yaml ps` plus inspect | Final service is running and healthy | PASS | Final container id `1da074e227df...`; `health=healthy`, `status=running`, `0.0.0.0:8088->8080`. |

### Step 9 backup command

```bash
docker compose -f compose.unraid.yaml run -T --rm krakked export-install \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --include-data \
  --output /krakked/state/krakked-first-backup.zip < /dev/null
```

### Step 10 restore check (non-destructive)

Restore into scratch paths under `state/` so the live config/db/data are left
untouched. Add `--force` only if the scratch target already has files (existing
targets are backed up first).

```bash
docker compose -f compose.unraid.yaml run -T --rm krakked import-install \
  --input /krakked/state/krakked-first-backup.zip \
  --config-dir /krakked/state/restore-check/config \
  --db-path /krakked/state/restore-check/portfolio.db \
  --data-dir /krakked/state/restore-check/data < /dev/null
```

## Overall result

- [x] All acceptance steps pass → Deployment Proof V1 achieved.
- [ ] Partial → record blockers in Findings and open narrow follow-ups.

Status: `PASS` (`13` pass, `0` fail, `0` warn).

## Findings / fixes needed

Capture anything the drill exposed — the point of V1 is to surface the practical
gaps, not just to get a green checklist. Likely candidates to watch:

- First-boot / setup fan-out friction or unclear prompts.
- Startup / unlock / session lifecycle clarity (can the operator tell slow
  warmup from a real fault?).
- Health check timing vs. real readiness.
- Backup/restore ergonomics and paths.
- Any permission / volume / appdata ownership issues specific to Unraid.

| Finding | Severity | Proposed fix / follow-up |
|---------|----------|--------------------------|
| Compose was missing on the Unraid host. | Medium | Installed Docker Compose CLI plugin `v5.1.4` at `/usr/local/lib/docker/cli-plugins/docker-compose`. Follow up by making the Compose install persistent across Unraid reboots. |
| Windows-written `.env` files can introduce CRLF and corrupt Compose image references. | Medium | Keep `.env` LF-only on the Unraid checkout. Consider normalizing `.env` in the bootstrap helper when preserving an existing file. |
| `krakked run-once` initially failed because the one-shot WebSocket stub did not expose every field expected by market-data status checks. | High | Fixed locally and mirrored to the Unraid source under test: `_WsStub` now exposes `subscription_status` and instance-owned cache fields. |
| `krakked run-once` needed explicit one-shot resource cleanup. | Medium | Fixed locally and mirrored to the Unraid source under test: `run_strategy_once()` now closes the portfolio store and shuts down market data in `finally`. |
| Compose one-shot commands can hang in Unraid `ttyd` unless stdin/TTY are detached. | Medium | Use `docker compose run -T ... < /dev/null` for proof-runner one-shots and documented backup/restore examples. |
| Final paper cycle logged two order-routing rejections. | Low | Not a deployment blocker: the forced-safe paper command returned successfully, live gates stayed closed, and synthetic store evidence was present. Track separately only if strategy/operator wording should be improved. |

## Out of scope for V1

- Live trading enablement (gates stay closed; live is a later operational drill).
- UI Simple/Advanced split — informed by what this drill shows the UI must
  explain, done after.
