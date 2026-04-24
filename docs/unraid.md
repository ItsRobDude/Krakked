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
```

If your Unraid install uses the older Compose command, replace `docker compose` with `docker-compose`. If Compose is not installed at all, the bootstrap helper falls back to plain `docker build` / `docker run`.

## If You Need To Reset The Generated Files

This overwrites the generated `.env`, `compose.unraid.yaml`, and seeded appdata config files:

```bash
bash scripts/unraid_bootstrap.sh --force --start
```

Use this only when you intentionally want the helper to recreate the starter files.

## Published Image Mode

The default helper mode builds from the local checkout because that is easiest while the product is still being proven. Later, for a pinned release image:

```bash
bash scripts/unraid_bootstrap.sh --mode image --start
```

Then edit `.env` if the image tag should be something other than the starter value:

```dotenv
KRAKKED_IMAGE=ghcr.io/itsrobdude/krakked
KRAKKED_IMAGE_TAG=v0.1.0
```

Keep image tags pinned. Do not rely on `latest` for a home server you want to trust.

## First Good Backup

After the UI boots successfully, make one export before experimenting:

```bash
docker compose -f compose.unraid.yaml run --rm krakked export-install \
  --config-dir /krakked/config \
  --db-path /krakked/state/portfolio.db \
  --data-dir /krakked/data \
  --include-data \
  --output /krakked/state/krakked-first-backup.zip
```

The export lands under:

```text
/mnt/user/appdata/krakked/state/krakked-first-backup.zip
```

## Cautions

- Start in paper mode on the server. Live mode is a later operational decision.
- The helper will not overwrite existing config unless you pass `--force`.
- If the helper warns that the git remote does not look like `ItsRobDude/krakked`, stop and check the folder before continuing.
- This streamlines the Unraid terminal path, but the actual Unraid host smoke test still needs to be run on your box.
