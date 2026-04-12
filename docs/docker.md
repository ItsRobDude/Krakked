# Docker Deployment

Krakked is designed to run as a self-hosted Docker deployment.

The primary path is `docker compose`, backed by three bind-mounted directories:

- `deploy/config` for `config.yaml`, `config.paper.yaml`, `config.live.yaml`, `secrets.enc`, and `accounts.yaml`
- `deploy/data` for OHLC/cache/metadata files
- `deploy/state` for `portfolio.db` and other local runtime state

## Two Docker Modes

Krakked supports two closely related Docker workflows:

- `compose.yaml` for running a published image such as `ghcr.io/<owner>/krakked:v0.1.0`
- `compose.yaml` + `compose.dev.yaml` for building the image from a local source checkout

If you are actively developing Krakked, use the dev override. If you are operating a released build, use the base compose file by itself.

## Quickstart (Source Checkout)

1. Copy the environment template:

```bash
cp .env.example .env
```

2. Create deployment directories:

```bash
mkdir -p deploy/config deploy/data deploy/state
```

3. Seed your config:

```bash
cp config_examples/config.yaml deploy/config/config.yaml
cp config_examples/config.paper.yaml deploy/config/config.paper.yaml
cp config_examples/config.live.yaml deploy/config/config.live.yaml
```

4. Merge the container-specific overrides from `config_examples/config.container.yaml` into `deploy/config/config.yaml`.

At minimum, ensure these values are present in the container config:

```yaml
market_data:
  ohlc_store:
    root_dir: "/krakked/data/ohlc"
  metadata_path: "/krakked/data/metadata.json"

portfolio:
  db_path: "/krakked/state/portfolio.db"
  auto_migrate_schema: false

ui:
  host: "0.0.0.0"
  port: 8080
```

5. Provide credentials using one of these approaches:

- Put `KRAKEN_API_KEY` / `KRAKEN_API_SECRET` in `.env`
- Or place `secrets.enc` in `deploy/config` and set `KRAKKED_SECRET_PW` in `.env`

6. Start Krakked from the local source tree:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build
```

7. Open the UI:

- [http://localhost:8080](http://localhost:8080)

## Quickstart (Published Image)

1. Copy the environment template:

```bash
cp .env.example .env
```

2. Set the published image name and tag in `.env`:

```dotenv
KRAKKED_IMAGE=ghcr.io/<owner>/krakked
KRAKKED_IMAGE_TAG=v0.1.0
```

3. Create deployment directories and seed config exactly as in the source-checkout flow.

4. Pull and start the pinned image:

```bash
docker compose pull
docker compose up -d
```

This is the recommended operator workflow for customers and non-developer installs because the image tag stays explicit and reproducible.

## Operational Notes

- `KRAKKED_CONFIG_DIR` and `KRAKKED_DATA_DIR` are supported environment overrides for container deployments.
- The container health check targets `GET /api/health`.
- `paper` is the recommended starting environment; treat it as staging for live trading, not the final product mode.
- Before switching to `live`, make sure the live overlay enables auth and points to the persisted state paths above.
- The image entrypoint is `krakked`, so operational helpers can be run directly through Compose:

```bash
docker compose run --rm krakked db-info --db-path /krakked/state/portfolio.db
docker compose run --rm krakked db-backup --db-path /krakked/state/portfolio.db
docker compose run --rm krakked export-install \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --include-data \
  --output /krakked/state/krakked-export.zip
```

- `deploy/state` is the safest default destination for backups and exports because it is already mounted and persisted.
- See [`docs/upgrades.md`](upgrades.md) before changing image tags and [`docs/backup-restore.md`](backup-restore.md) for recovery workflows.
- If your target host is Unraid, see [`docs/unraid.md`](unraid.md) for a translated version of this flow using persistent share paths.
