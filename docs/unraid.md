# Unraid Deployment Notes

This guide is a practical translation of Krakked's Docker flow for an Unraid-style home server. It does not replace [`docs/docker.md`](docker.md); it simply maps the same deployment to the directories and habits that are common on Unraid.

## Recommended Shape

Use a dedicated appdata-style location for Krakked so config, state, and cached data survive container restarts and image upgrades.

Example host layout:

- `/mnt/user/appdata/krakked/config`
- `/mnt/user/appdata/krakked/data`
- `/mnt/user/appdata/krakked/state`

Keep your compose file, `.env`, and any release notes in a small project folder that is easy to back up separately.

## Suggested First Pass

1. Create the directories above on the Unraid host.
2. Copy in:
   - `compose.yaml`
   - `.env.example` as `.env`
   - the seed config files from `config_examples/`
3. Set a pinned image tag in `.env`:

```dotenv
KRAKKED_IMAGE=ghcr.io/<owner>/krakked
KRAKKED_IMAGE_TAG=v0.1.0
```

4. Update the volume mappings in `compose.yaml` from the repo-local `./deploy/...` paths to your Unraid paths.

A typical translation looks like:

```yaml
volumes:
  - /mnt/user/appdata/krakked/config:/krakked/config
  - /mnt/user/appdata/krakked/data:/krakked/data
  - /mnt/user/appdata/krakked/state:/krakked/state
```

5. Merge the container path overrides from `config_examples/config.container.yaml` into your running `config.yaml`.
6. Start in paper mode first.

## Why This Layout Works Well

- appdata-style paths are persistent across container recreation
- backups are straightforward because config, cached data, and SQLite state live in predictable places
- upgrades stay simple because you generally only change the image tag and restart

## Operational Commands

Once the container is running, the same helper commands from the Docker docs still apply:

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

## Cautions

- Keep the image tag pinned; don't rely on `latest` for a home server you want to trust.
- Export before upgrades.
- Start with paper mode even on the server; treat live mode as a later operational decision, not a first boot default.
- I have not container-smoke-tested this on your Unraid box from here, so use this as a prepared path, not a claim of verified host compatibility.
