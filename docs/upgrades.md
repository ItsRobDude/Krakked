# Upgrades And Migrations

Krakked should be upgraded like an appliance: back it up, change the image tag, bring the service back up, then verify health.

For release-candidate upgrades, Unraid proof runs, or paper soaks, complete
[`deployment-preflight-checklist.md`](./deployment-preflight-checklist.md)
before changing the running host. That checklist records the target SHA, image,
expected provenance, active profile, DB path, and evidence artifacts.

## Safe Upgrade Flow

1. Export the current install before touching the image:

```bash
docker compose run --rm krakked export-install \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --include-data \
  --output /krakked/state/krakked-pre-upgrade.zip
```

2. Optionally create a timestamped database-only backup too:

```bash
docker compose run --rm krakked db-backup --db-path /krakked/state/portfolio.db
```

3. Update `.env` to the new published image tag:

```dotenv
KRAKKED_IMAGE=ghcr.io/itsrobdude/krakked
KRAKKED_IMAGE_TAG=v0.1.1
KRAKKED_EXPECTED_IMAGE=ghcr.io/itsrobdude/krakked
KRAKKED_EXPECTED_IMAGE_TAG=v0.1.1
KRAKKED_EXPECTED_RUNTIME_SOURCE=image
# Optional but recommended when known:
KRAKKED_EXPECTED_BUILD_GIT_SHA=<release-commit-sha>
```

4. Pull and restart the stack:

```bash
docker compose pull
docker compose up -d
```

5. Verify the running system:

```bash
docker compose ps
docker compose logs --tail=100 krakked
curl -fsS http://<host>:8088/api/health
```

The health payload should report `runtime_source=image`, the expected image tag,
and `deployment_drift_detected=false`.

6. Check the schema version and DB integrity if the release changed persistence code:

```bash
docker compose run --rm krakked db-schema-version --db-path /krakked/state/portfolio.db
docker compose run --rm krakked db-check --db-path /krakked/state/portfolio.db
```

## Rollback

If a release misbehaves:

1. Stop the stack: `docker compose down`
2. Revert `.env` to the previous `KRAKKED_IMAGE_TAG`
3. Start the older image again: `docker compose up -d`
4. If the data/config were already changed in a bad way, restore from the export archive using [`docs/backup-restore.md`](backup-restore.md)

For release sign-off, practice the whole upgrade and rollback using the Unraid
wrapper before asking operators to trust the tag:

```bash
bash scripts/unraid_image_upgrade_rollback_drill.sh \
  --image ghcr.io/itsrobdude/krakked \
  --from-tag <current-tag> \
  --to-tag <candidate-tag> \
  --from-sha <current-sha> \
  --to-sha <candidate-sha> \
  --host-url http://<unraid-ip>:8088
```

The drill must report `IMAGE_UPGRADE_ROLLBACK_RESULT=PASS`, `fail=0`, and
phase proofs with `skip_run_once=false`, `skip_restore=false`, and
`deployment_drift_detected=false`.

## Migration Notes Template

For every operator-facing release, document:

- required config changes
- database schema changes
- new environment variables
- removed or renamed CLI commands
- any manual recovery steps

## Current Baseline

The `0.1.x` line assumes:

- the internal package/config namespace is `krakked`
- Docker is the primary deployment path
- export/import archives use `format_version: 1`
- published-image deployments should expose runtime provenance and expected
  provenance in health payloads

Any future breaking change to those assumptions should be called out here before release.
