# Backup, Export, And Restore

Krakked now supports two levels of protection:

- fast SQLite-only backups with `db-backup`
- full install exports with `export-install` / `import-install`

Use the full export before upgrades, migrations, or machine moves.

## Database-Only Backup

For a quick portfolio store backup:

```bash
docker compose run --rm krakked db-backup --db-path /krakked/state/portfolio.db
```

This creates a timestamped `.bak` file and uses SQLite's backup API so WAL-backed changes are captured safely.

## Full Install Export

To capture config, the portfolio database, and cached data in one archive:

```bash
docker compose run --rm krakked export-install \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --include-data \
  --output /krakked/state/krakked-export.zip
```

The archive contains:

- `manifest.json`
- `config/`
- `state/portfolio.db`
- `data/` when `--include-data` is used

## Restore Onto A New Machine Or Deployment

1. Copy the archive onto the new host.
2. Make sure the target `deploy/config`, `deploy/data`, and `deploy/state` directories exist.
3. Restore the archive:

```bash
docker compose run --rm krakked import-install \
  --input /krakked/state/krakked-export.zip \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data
```

If files already exist and you intentionally want to overwrite them:

```bash
docker compose run --rm krakked import-install \
  --input /krakked/state/krakked-export.zip \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --force
```

When `--force` is used, existing files are backed up first with timestamped `.bak` copies.

## Suggested Operator Habit

- export before every upgrade
- keep at least one off-machine copy of important exports
- use the DB-only backup command for quick checkpoints during debugging
- test restore on a spare machine before you depend on it in production
