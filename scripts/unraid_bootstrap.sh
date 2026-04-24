#!/usr/bin/env bash
set -euo pipefail

APPDATA_DIR="${KRAKKED_UNRAID_APPDATA_DIR:-/mnt/user/appdata/krakked}"
COMPOSE_FILE="${KRAKKED_UNRAID_COMPOSE_FILE:-compose.unraid.yaml}"
MODE="source"
START_STACK="false"
FORCE="false"

usage() {
  cat <<'USAGE'
Krakked Unraid bootstrap

Usage:
  bash scripts/unraid_bootstrap.sh [--start] [--force] [--mode source|image]

Defaults:
  --mode source   Build from this checkout.
  --start         Also start the container after writing files.
  --force         Overwrite generated compose/.env and seeded config files.

Environment overrides:
  KRAKKED_UNRAID_APPDATA_DIR=/mnt/user/appdata/krakked
  KRAKKED_UNRAID_COMPOSE_FILE=compose.unraid.yaml
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --start)
      START_STACK="true"
      ;;
    --force)
      FORCE="true"
      ;;
    --mode)
      shift
      MODE="${1:-}"
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1"
      usage
      exit 2
      ;;
  esac
  shift
done

if [ "$MODE" != "source" ] && [ "$MODE" != "image" ]; then
  echo "ERROR: --mode must be either 'source' or 'image'."
  exit 2
fi

say() {
  printf '\n==> %s\n' "$1"
}

fail() {
  printf '\nERROR: %s\n' "$1" >&2
  exit 1
}

detect_runtime() {
  if docker compose version >/dev/null 2>&1; then
    RUNTIME_MODE="compose"
    COMPOSE_DISPLAY="docker compose"
    return
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    RUNTIME_MODE="compose"
    COMPOSE_DISPLAY="docker-compose"
    return
  fi
  if command -v docker >/dev/null 2>&1; then
    RUNTIME_MODE="docker"
    COMPOSE_DISPLAY=""
    return
  fi
  fail "Docker was not found."
}

run_compose() {
  if [ "$COMPOSE_DISPLAY" = "docker compose" ]; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

env_value() {
  local key="$1"
  local default="$2"

  if [ -f .env ]; then
    local value
    value="$(awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); found=1 } END { if (!found) exit 1 }' .env || true)"
    if [ -n "$value" ]; then
      printf '%s' "$value"
      return
    fi
  fi

  printf '%s' "$default"
}

ensure_unraid_port() {
  if [ ! -f .env ]; then
    return
  fi

  if ! grep -q '^KRAKKED_PORT=' .env; then
    printf '\nKRAKKED_PORT=8088\n' >> .env
    echo "Added KRAKKED_PORT=8088 to .env"
    return
  fi

  if grep -q '^KRAKKED_PORT=8080$' .env; then
    sed -i 's/^KRAKKED_PORT=8080$/KRAKKED_PORT=8088/' .env
    echo "Updated .env KRAKKED_PORT from 8080 to 8088 because Unraid already uses 8080."
  fi
}

run_docker_start() {
  local image_ref
  local host_port
  local ui_host
  local ui_port
  local env_name

  image_ref="$(env_value KRAKKED_IMAGE "$image_name"):$(env_value KRAKKED_IMAGE_TAG "$image_tag")"
  host_port="$(env_value KRAKKED_PORT 8088)"
  ui_host="$(env_value KRAKKED_UI_HOST 0.0.0.0)"
  ui_port="$(env_value KRAKKED_UI_PORT 8080)"
  env_name="$(env_value KRAKKED_ENV paper)"

  if [ "$MODE" = "source" ]; then
    docker build -t "$image_ref" .
  else
    docker pull "$image_ref"
  fi

  if docker container inspect krakked >/dev/null 2>&1; then
    if [ "$FORCE" = "true" ]; then
      docker rm -f krakked
    else
      echo "Existing krakked container found; starting it without replacing it."
      echo "To rebuild/recreate the container, rerun with --force --start."
      docker start krakked >/dev/null
      return
    fi
  fi

  docker run -d \
    --name krakked \
    --restart unless-stopped \
    -p "${host_port}:8080" \
    -e "KRAKKED_ENV=${env_name}" \
    -e "KRAKKED_CONFIG_DIR=/krakked/config" \
    -e "KRAKKED_DATA_DIR=/krakked/data" \
    -e "KRAKKED_UI_HOST=${ui_host}" \
    -e "KRAKKED_UI_PORT=${ui_port}" \
    -e "KRAKKED_SECRET_PW=$(env_value KRAKKED_SECRET_PW "")" \
    -e "KRAKEN_API_KEY=$(env_value KRAKEN_API_KEY "")" \
    -e "KRAKEN_API_SECRET=$(env_value KRAKEN_API_SECRET "")" \
    -e "UI_DIST_DIR=/app/ui-dist" \
    -v "${APPDATA_DIR}/config:/krakked/config" \
    -v "${APPDATA_DIR}/data:/krakked/data" \
    -v "${APPDATA_DIR}/state:/krakked/state" \
    "$image_ref"
}

write_file() {
  local path="$1"
  local contents="$2"

  if [ -e "$path" ] && [ "$FORCE" != "true" ]; then
    echo "Keeping existing $path"
    return
  fi

  printf '%s\n' "$contents" > "$path"
  echo "Wrote $path"
}

seed_file() {
  local source="$1"
  local target="$2"

  if [ -e "$target" ] && [ "$FORCE" != "true" ]; then
    echo "Keeping existing $target"
    return
  fi

  cp "$source" "$target"
  echo "Seeded $target"
}

say "Checking that this is the Krakked repo"
for required in .env.example compose.yaml compose.dev.yaml Dockerfile config_examples/config.yaml config_examples/config.paper.yaml config_examples/config.live.yaml; do
  [ -e "$required" ] || fail "Missing $required. cd into the real Krakked checkout first."
done

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  remote_urls="$(git remote -v 2>/dev/null || true)"
  if ! printf '%s\n' "$remote_urls" | grep -qi 'ItsRobDude/krakked'; then
    echo "WARNING: git remote does not look like ItsRobDude/krakked:"
    printf '%s\n' "$remote_urls"
    echo "Continuing because the expected Krakked files are present."
  fi
fi

say "Creating persistent Unraid folders"
mkdir -p \
  "$APPDATA_DIR/config" \
  "$APPDATA_DIR/data" \
  "$APPDATA_DIR/state"
echo "Using appdata root: $APPDATA_DIR"

say "Seeding config files"
if [ ! -e "$APPDATA_DIR/config/config.yaml" ] || [ "$FORCE" = "true" ]; then
  awk '
    $0 == "portfolio:" {
      print
      print "  db_path: \"/krakked/state/portfolio.db\""
      next
    }
    {
      gsub(/root_dir: "~\/.local\/share\/krakked\/ohlc"/, "root_dir: \"/krakked/data/ohlc\"")
      gsub(/metadata_path: "~\/.local\/share\/krakked\/metadata.json"/, "metadata_path: \"/krakked/data/metadata.json\"")
      gsub(/host: "127.0.0.1"/, "host: \"0.0.0.0\"")
      print
    }
  ' config_examples/config.yaml > "$APPDATA_DIR/config/config.yaml"
  echo "Seeded $APPDATA_DIR/config/config.yaml with container paths"
else
  echo "Keeping existing $APPDATA_DIR/config/config.yaml"
fi

seed_file "config_examples/config.paper.yaml" "$APPDATA_DIR/config/config.paper.yaml"
seed_file "config_examples/config.live.yaml" "$APPDATA_DIR/config/config.live.yaml"

say "Writing Unraid Compose file"
if [ "$MODE" = "source" ]; then
  build_block='    build:
      context: .'
else
  build_block=''
fi

write_file "$COMPOSE_FILE" "services:
  krakked:
    image: \${KRAKKED_IMAGE:-krakked}:\${KRAKKED_IMAGE_TAG:-unraid-local}
${build_block}
    restart: unless-stopped
    ports:
      - \"\${KRAKKED_PORT:-8088}:8080\"
    environment:
      KRAKKED_ENV: \${KRAKKED_ENV:-paper}
      KRAKKED_CONFIG_DIR: /krakked/config
      KRAKKED_DATA_DIR: /krakked/data
      KRAKKED_UI_HOST: \${KRAKKED_UI_HOST:-0.0.0.0}
      KRAKKED_UI_PORT: \${KRAKKED_UI_PORT:-8080}
      KRAKKED_SECRET_PW: \${KRAKKED_SECRET_PW:-}
      KRAKEN_API_KEY: \${KRAKEN_API_KEY:-}
      KRAKEN_API_SECRET: \${KRAKEN_API_SECRET:-}
      UI_DIST_DIR: /app/ui-dist
    volumes:
      - ${APPDATA_DIR}/config:/krakked/config
      - ${APPDATA_DIR}/data:/krakked/data
      - ${APPDATA_DIR}/state:/krakked/state
    healthcheck:
      test:
        [
          \"CMD\",
          \"python\",
          \"-c\",
          \"import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/health', timeout=5).read()\",
        ]
      interval: 30s
      timeout: 5s
      retries: 5
      start_period: 20s"

say "Writing .env"
if [ "$MODE" = "source" ]; then
  image_name="krakked"
  image_tag="unraid-local"
else
  image_name="ghcr.io/itsrobdude/krakked"
  image_tag="v0.1.0"
fi

write_file ".env" "KRAKKED_IMAGE=${image_name}
KRAKKED_IMAGE_TAG=${image_tag}
KRAKKED_PORT=8088
KRAKKED_UI_HOST=0.0.0.0
KRAKKED_UI_PORT=8080
KRAKKED_ENV=paper
KRAKKED_SECRET_PW=
KRAKEN_API_KEY=
KRAKEN_API_SECRET="

ensure_unraid_port
detect_runtime

say "Sanity check"
if [ "$RUNTIME_MODE" = "compose" ]; then
  run_compose -f "$COMPOSE_FILE" config >/dev/null
  echo "Compose file is valid."
else
  docker version >/dev/null
  echo "Docker is available. Compose is not installed, so the helper will use plain Docker."
fi

if [ "$START_STACK" = "true" ]; then
  say "Starting Krakked"
  if [ "$RUNTIME_MODE" = "compose" ]; then
    if [ "$MODE" = "source" ]; then
      run_compose -f "$COMPOSE_FILE" up -d --build
    else
      run_compose -f "$COMPOSE_FILE" pull
      run_compose -f "$COMPOSE_FILE" up -d
    fi
  else
    run_docker_start
  fi
else
  say "Ready"
  echo "Start when ready:"
  if [ "$RUNTIME_MODE" = "compose" ] && [ "$MODE" = "source" ]; then
    echo "  $COMPOSE_DISPLAY -f $COMPOSE_FILE up -d --build"
  elif [ "$RUNTIME_MODE" = "compose" ]; then
    echo "  $COMPOSE_DISPLAY -f $COMPOSE_FILE pull"
    echo "  $COMPOSE_DISPLAY -f $COMPOSE_FILE up -d"
  else
    echo "  bash scripts/unraid_bootstrap.sh --start"
  fi
fi

if [ "$RUNTIME_MODE" = "compose" ]; then
cat <<EOF

Next checks:
  $COMPOSE_DISPLAY -f $COMPOSE_FILE ps
  $COMPOSE_DISPLAY -f $COMPOSE_FILE logs --tail=100 krakked

Open the UI:
  http://<your-unraid-ip>:8088

Back up after first successful boot:
  $COMPOSE_DISPLAY -f $COMPOSE_FILE run --rm krakked export-install --config-dir /krakked/config --db-path /krakked/state/portfolio.db --data-dir /krakked/data --include-data --output /krakked/state/krakked-first-backup.zip
EOF
else
cat <<EOF

Next checks:
  docker ps --filter name=krakked
  docker logs --tail=100 krakked

Open the UI:
  http://<your-unraid-ip>:8088

Back up after first successful boot:
  docker run --rm -v ${APPDATA_DIR}/config:/krakked/config -v ${APPDATA_DIR}/data:/krakked/data -v ${APPDATA_DIR}/state:/krakked/state $(env_value KRAKKED_IMAGE "$image_name"):$(env_value KRAKKED_IMAGE_TAG "$image_tag") export-install --config-dir /krakked/config --db-path /krakked/state/portfolio.db --data-dir /krakked/data --include-data --output /krakked/state/krakked-first-backup.zip
EOF
fi
