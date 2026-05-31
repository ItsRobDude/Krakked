# Deployment Proof V1 (Unraid, paper mode)

First product-pivot deliverable. The goal is to prove that Krakked actually runs
end to end as a Docker-first self-hosted product on a real Unraid host — first
boot, persistence, paper safety, operator visibility, backup/restore, and
live-gates-closed-by-default — and to capture any gaps the drill exposes.

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

- Commit / image tag: `__________`
- Date run: `__________`
- Unraid version / Docker + Compose version: `__________`

## Prerequisites

- Repo checked out on the Unraid host (the bootstrap verifies the
  `ItsRobDude/krakked` remote before doing setup).
- Docker (and ideally Compose) available; the helper falls back to plain
  `docker build` / `docker run` if Compose is absent.
- A `KRAKKED_SECRET_PW` value for the encrypted secrets store. Live Kraken API
  keys are **optional** for this paper drill (paper mode uses the synthetic
  wallet and does not transmit live orders).

## Acceptance criteria / drill steps

Run top to bottom. Each step lists the command/check and what "pass" looks like.

| # | Step | Command / check | Pass condition | Result | Notes |
|---|------|-----------------|----------------|--------|-------|
| 1 | First boot | `bash scripts/unraid_bootstrap.sh --start` | Helper seeds appdata config, writes `compose.unraid.yaml` + `.env`, builds, and starts the container with no errors | | |
| 2 | Container healthy | `docker compose -f compose.unraid.yaml ps` | `krakked` is `running` and reports `healthy` (after the ~20s start period) | | |
| 3 | Mounts created & populated | `ls /mnt/user/appdata/krakked/config /mnt/user/appdata/krakked/state` | `config.yaml` / `config.paper.yaml` present in config; state dir writable | | |
| 4 | Health endpoint | `curl -fsS http://<unraid-ip>:8088/api/health` | Returns a healthy response (HTTP 200) | | |
| 5 | UI reachable | Open `http://<unraid-ip>:8088` in a browser | Dashboard loads; startup/unlock state is legible (warmup is clearly distinguishable from a fault) | | |
| 6 | Paper safety cycle | `docker compose -f compose.unraid.yaml run --rm krakked run-once` | One forced-safe paper cycle completes; no live order submission; orders/results recorded to the synthetic store | | |
| 7 | Live gates closed by default | Inspect runtime/config (UI mode indicator and `config.paper.yaml`) | `execution.mode=paper`, `allow_live_trading=false`; UI shows paper mode | | |
| 8 | Restart persistence | `docker compose -f compose.unraid.yaml down` then `up -d` | Container comes back healthy; `state/portfolio.db` and config survive the restart (state from step 6 still present) | | |
| 9 | First backup / export | `export-install` (see command below) | Backup archive lands at `/mnt/user/appdata/krakked/state/krakked-first-backup.zip` | | |
| 10 | Restore round-trip | `import-install` of the archive into a scratch location | Import succeeds and reports the restored config/state | | |
| 11 | Recreate after change | `bash scripts/unraid_bootstrap.sh --recreate --start` | Container rebuilds/recreates while keeping existing appdata config (no `--force`) | | |

### Step 9 backup command

```bash
docker compose -f compose.unraid.yaml run --rm krakked export-install \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --include-data \
  --output /krakked/state/krakked-first-backup.zip
```

### Step 10 restore check (non-destructive)

Restore into scratch paths under `state/` so the live config/db/data are left
untouched. Add `--force` only if the scratch target already has files (existing
targets are backed up first).

```bash
docker compose -f compose.unraid.yaml run --rm krakked import-install \
  --input /krakked/state/krakked-first-backup.zip \
  --config-dir /krakked/state/restore-check/config \
  --db-path /krakked/state/restore-check/portfolio.db \
  --data-dir /krakked/state/restore-check/data
```

## Overall result

- [ ] All acceptance steps pass → Deployment Proof V1 achieved.
- [ ] Partial → record blockers in Findings and open narrow follow-ups.

Status: `__________`

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
| | | |

## Out of scope for V1

- Live trading enablement (gates stay closed; live is a later operational drill).
- Published-image mode (`--mode image`) — V1 proves the local-checkout build
  path; pinned-image proof is a follow-up.
- UI Simple/Advanced split — informed by what this drill shows the UI must
  explain, done after.
