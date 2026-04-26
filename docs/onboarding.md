# Onboarding Guide

This guide is for the first-time operator who wants Krakked running without having to learn the whole repo first.

## Best Starting Point

Start in paper mode with Docker Desktop or Docker Engine, get the UI reachable, and make one backup before you experiment further.

Important current behavior:

- paper mode uses practice money in a local Krakked paper wallet, not your current Kraken balances
- the paper wallet starts with a local $10,000 USD bankroll by default and persists by profile
- live exchange balances are optional comparison context only, not the source of paper-mode funds
- normal paper config uses `execution.validate_only: false`; live submission is still blocked because paper mode is not live and `allow_live_trading` remains closed
- during startup or portfolio sync, some dashboard panels may show local degraded or pending states instead of blocking the whole UI

## What You Need

- Docker Desktop (Windows/macOS) or Docker Engine (Linux)
- Kraken API credentials
- a copy of this repo, or a published Krakked image and compose bundle

## Fast First Run

1. Copy the environment file:

```bash
cp .env.example .env
```

2. Create the persistent directories:

```bash
mkdir -p deploy/config deploy/data deploy/state
```

3. Seed the config files:

```bash
cp config_examples/config.yaml deploy/config/config.yaml
cp config_examples/config.paper.yaml deploy/config/config.paper.yaml
cp config_examples/config.live.yaml deploy/config/config.live.yaml
```

4. Merge the container path overrides from [`config_examples/config.container.yaml`](../config_examples/config.container.yaml) into `deploy/config/config.yaml`.

5. Put your Kraken credentials in `.env`, or create `deploy/config/secrets.enc` with:

```bash
docker compose run --rm krakked setup
```

6. Start Krakked:

- From a source checkout:

```bash
docker compose -f compose.yaml -f compose.dev.yaml up --build
```

- From a published image:

```bash
docker compose pull
docker compose up -d
```

7. Open [http://localhost:8080](http://localhost:8080).

## First Things To Verify

- the container stays healthy
- the UI loads
- the config is mounted from `deploy/config`
- the database appears in `deploy/state`
- the bot is still in paper mode
- the paper overview shows the local Krakked paper wallet balance and any optional exchange comparison separately
- the session can reach the active cockpit even if some panels are still warming up or timing out locally

## First Safety Habit

Create a backup/export before switching configs or image tags:

```bash
docker compose run --rm krakked export-install \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --include-data \
  --output /krakked/state/krakked-first-backup.zip
```

## Beginner-Friendly Operating Rhythm

- stay in paper mode while validating strategy weights and UI controls
- treat paper balances as Krakked practice money for testing strategy behavior, not as a mirror of your Kraken account
- keep image tags pinned to a known version
- export before upgrades
- only move toward live trading after you trust the logs, metrics, and risk settings

## What To Expect In The UI Today

- `Paper Trading Overview` should load as the main operator cockpit after unlock and session start
- the active cockpit should refresh from the cockpit snapshot where available, with local degraded messages for sections that cannot load
- strategy, risk, and portfolio panels may still show temporary timeout/degraded states while the active session is warming up
- if those degraded states persist, treat that as an operator issue to investigate before relying on the dashboard for trading decisions

## When To Use Advanced Features

Once the basic stack is stable, move on to:

- strategy weighting and attribution
- ML strategy toggles
- live-trading readiness drills
- upgrade and rollback practice

Those workflows are documented separately so the first-run path stays simple.
