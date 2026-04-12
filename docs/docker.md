# Docker Deployment

Krakked is designed to run as a self-hosted Docker deployment.

The primary path is `docker compose`, backed by three bind-mounted directories:

- `deploy/config` for `config.yaml`, `config.paper.yaml`, `config.live.yaml`, `secrets.enc`, and `accounts.yaml`
- `deploy/data` for OHLC/cache/metadata files
- `deploy/state` for `portfolio.db` and other local runtime state

## Quickstart

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

6. Start Krakked:

```bash
docker compose up --build
```

7. Open the UI:

- [http://localhost:8080](http://localhost:8080)

## Operational Notes

- `KRAKKED_CONFIG_DIR` and `KRAKKED_DATA_DIR` are supported environment overrides for container deployments.
- The container health check targets `GET /api/health`.
- `paper` is the recommended starting environment; treat it as staging for live trading, not the final product mode.
- Before switching to `live`, make sure the live overlay enables auth and points to the persisted state paths above.
