# Unraid Deployment Notes

This is the low-terminal path for Krakked on an Unraid-style home server. The goal is one copy/paste command, then plain output that tells you what happened.

## Easiest Path

Open the Unraid terminal, get into the Krakked repo checkout, then run:

```bash
bash scripts/unraid_bootstrap.sh --start
```

That helper does the fussy parts for you:

- verifies you are in the real Krakked checkout before doing setup
- creates `/mnt/user/appdata/krakked/config`, `/data`, and `/state`
- seeds paper/live config files with container-safe paths
- writes `compose.unraid.yaml` with Unraid appdata volume mounts
- writes a simple `.env`
- validates the Compose file when Compose is installed, or falls back to plain Docker when it is not
- starts Krakked in paper mode when `--start` is present

Open the UI after it starts:

```text
http://<your-unraid-ip>:8088
```

## The Only Commands You Should Need

From the Krakked repo folder on Unraid:

```bash
# First setup and start
bash scripts/unraid_bootstrap.sh --start

# See whether the container is running
docker compose -f compose.unraid.yaml ps

# See the latest logs
docker compose -f compose.unraid.yaml logs --tail=100 krakked

# Stop
docker compose -f compose.unraid.yaml down

# Start again
docker compose -f compose.unraid.yaml up -d

# Rebuild/recreate after code changes, keeping existing config files
bash scripts/unraid_bootstrap.sh --recreate --start

# Run the current-main deployment proof and write a log under appdata/state
bash scripts/unraid_deployment_proof.sh --host-url http://<your-unraid-ip>:8088
```

If your Unraid install uses the older Compose command, replace `docker compose` with `docker-compose`. If Compose is not installed at all, the bootstrap helper falls back to plain `docker build` / `docker run`.

## If You Need To Reset The Generated Files

This overwrites the generated `.env`, `compose.unraid.yaml`, and seeded appdata config files:

```bash
bash scripts/unraid_bootstrap.sh --force --start
```

Use this only when you intentionally want the helper to recreate the starter files.

## Published Image Mode

The default helper mode builds from the local checkout because it is convenient
while developing. For operator-style installs and release sign-off, use a pinned
published image:

```bash
bash scripts/unraid_bootstrap.sh --mode image --start --recreate
```

Then edit `.env` if the image tag should be something other than the starter value:

```dotenv
KRAKKED_IMAGE=ghcr.io/itsrobdude/krakked
KRAKKED_IMAGE_TAG=v0.1.1
```

Keep image tags pinned. Do not rely on `latest` for a home server you want to
trust. The running app reports `runtime_source`, image name/tag, build SHA, and
deployment drift fields in `/api/health` and `/api/system/health`; image-mode
proofs fail when those values do not match expectations.

Before using a published image for release sign-off, proof, or soak work, run
the repo-level
[`deployment-preflight-checklist.md`](./deployment-preflight-checklist.md). It
requires the intended image tag and `/api/health.build_git_sha` to match before
the session starts.

Docker health on Unraid is useful but not the final truth source. If Docker
reports the container as unhealthy while the container is running and
`http://<unraid-ip>:8088/api/health` returns OK, treat app HTTP health as
authoritative and inspect Docker/Unraid exec plumbing separately.

## First Good Backup

After the UI boots successfully, make one export before experimenting:

```bash
docker compose -f compose.unraid.yaml run -T --rm krakked export-install \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --include-data \
  --output /krakked/state/krakked-first-backup.zip < /dev/null
```

Use `run -T ... < /dev/null` for any one-shot `docker compose run` on Unraid:
without detaching the TTY/stdin, one-shot commands can hang in the Unraid web
terminal (`ttyd`).

The export lands under:

```text
/mnt/user/appdata/krakked/state/krakked-first-backup.zip
```

Before exporting a profile-backed paper session, open the UI health/operator
paths view and confirm the active portfolio DB path. Use that exact value for
`--db-path`; profile-scoped paper sessions may not use `/krakked/state/portfolio.db`.

## Current-main Proof

After pulling a new commit or before treating a source checkout as development
ready, run:

```bash
git pull --ff-only
bash scripts/unraid_deployment_proof.sh --host-url http://<your-unraid-ip>:8088
```

The proof runner recreates the paper container by default while preserving
existing appdata config. It checks health, UI reachability, persisted mounts,
Docker Compose reboot persistence, forced-safe paper `run-once`, synthetic store
evidence, live gates, restart persistence, export, and restore into scratch
paths. It prints
`DEPLOYMENT_PROOF_RESULT=PASS` only when all required checks pass.

For release/operator sign-off, prefer image mode with explicit expected
provenance:

```bash
bash scripts/unraid_deployment_proof.sh \
  --mode image \
  --image ghcr.io/itsrobdude/krakked \
  --image-tag v0.1.1 \
  --expected-build-git-sha <release-commit-sha> \
  --host-url http://<your-unraid-ip>:8088
```

For an upgrade/rollback drill using the same persistent appdata:

```bash
bash scripts/unraid_image_upgrade_rollback_drill.sh \
  --image ghcr.io/itsrobdude/krakked \
  --from-tag v0.1.1 \
  --to-tag v0.1.2 \
  --from-sha <from-release-sha> \
  --to-sha <to-release-sha> \
  --host-url http://<your-unraid-ip>:8088
```

The drill must end with `IMAGE_UPGRADE_ROLLBACK_RESULT=PASS`, `fail=0`, and
three phase summaries whose hard checks were not skipped.

If the proof is part of a longer paper soak, finish the deployment preflight
first and record the active profile, active DB path, monitor output path, image
tag, and build SHA in the soak report.

## Docker Compose boot persistence

Unraid's runtime plugin directory is not the durable source of truth across
reboots. Krakked keeps the Docker Compose CLI plugin on the flash-backed
`/boot/config` path and restores it through a marked `/boot/config/go` block.

Check the current host without changing it:

```bash
bash scripts/unraid_compose_persistence.sh check
```

Install or refresh that boot contract:

```bash
bash scripts/unraid_compose_persistence.sh install
```

Repair only the runtime plugin from the flash-backed copy:

```bash
bash scripts/unraid_compose_persistence.sh repair-runtime
```

The deployment proof records the runtime path, flash path, SHA-256 hashes,
hash-match status, Compose version, and whether the boot restore block is
present.

## Cautions

- Start in paper mode on the server. Live mode is a later operational decision.
- The helper will not overwrite existing config unless you pass `--force`.
- If the helper warns that the git remote does not look like `ItsRobDude/krakked`, stop and check the folder before continuing.
- The bootstrap now normalizes `.env` to LF on each run. If you hand-edit `.env`
  from Windows, keep it LF-only — a trailing CR on `KRAKKED_IMAGE` corrupts the
  Compose image reference.
- If Docker Compose stops working after a reboot, run
  `bash scripts/unraid_compose_persistence.sh check` first. Use `install` to
  restore the flash-backed boot contract or `repair-runtime` when only the
  runtime plugin path is missing.
- The latest recorded Unraid pinned-image upgrade/rollback proof is in
  [`deployment-proof.md`](./deployment-proof.md). Add new dated proof records
  there when release candidates are promoted.
