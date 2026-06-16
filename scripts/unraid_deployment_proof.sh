#!/usr/bin/env bash
set -uo pipefail

APPDATA_DIR="${KRAKKED_UNRAID_APPDATA_DIR:-/mnt/user/appdata/krakked}"
COMPOSE_FILE="${KRAKKED_UNRAID_COMPOSE_FILE:-compose.unraid.yaml}"
HOST_URL="${KRAKKED_PROOF_HOST_URL:-http://127.0.0.1:8088}"
MODE="source"
RECREATE="true"
SKIP_RUN_ONCE="false"
SKIP_RESTORE="false"
WAIT_TIMEOUT_SECONDS="${KRAKKED_PROOF_WAIT_TIMEOUT_SECONDS:-180}"

usage() {
  cat <<'USAGE'
Krakked Unraid deployment proof

Usage:
  bash scripts/unraid_deployment_proof.sh [options]

Options:
  --host-url URL          URL to test, default http://127.0.0.1:8088
  --appdata-dir PATH     Appdata root, default /mnt/user/appdata/krakked
  --compose-file PATH    Compose file, default compose.unraid.yaml
  --mode source|image    Pass through to unraid_bootstrap.sh, default source
  --no-recreate          Start without forcing container recreation
  --skip-run-once        Skip the forced-safe paper run-once check
  --skip-restore         Skip the export/import restore round-trip
  --wait-timeout SECONDS Seconds to wait for container/API readiness, default 180
  -h, --help             Show this help

Environment overrides:
  KRAKKED_UNRAID_APPDATA_DIR=/mnt/user/appdata/krakked
  KRAKKED_UNRAID_COMPOSE_FILE=compose.unraid.yaml
  KRAKKED_PROOF_HOST_URL=http://127.0.0.1:8088
  KRAKKED_PROOF_WAIT_TIMEOUT_SECONDS=180
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --host-url)
      shift
      HOST_URL="${1:-}"
      ;;
    --appdata-dir)
      shift
      APPDATA_DIR="${1:-}"
      ;;
    --compose-file)
      shift
      COMPOSE_FILE="${1:-}"
      ;;
    --mode)
      shift
      MODE="${1:-}"
      ;;
    --no-recreate)
      RECREATE="false"
      ;;
    --skip-run-once)
      SKIP_RUN_ONCE="true"
      ;;
    --skip-restore)
      SKIP_RESTORE="true"
      ;;
    --wait-timeout)
      shift
      WAIT_TIMEOUT_SECONDS="${1:-}"
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "ERROR: Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
  shift
done

if [ -z "$HOST_URL" ] || [ -z "$APPDATA_DIR" ] || [ -z "$COMPOSE_FILE" ]; then
  echo "ERROR: --host-url, --appdata-dir, and --compose-file must be non-empty." >&2
  exit 2
fi

if [ "$MODE" != "source" ] && [ "$MODE" != "image" ]; then
  echo "ERROR: --mode must be either 'source' or 'image'." >&2
  exit 2
fi

if ! printf '%s' "$WAIT_TIMEOUT_SECONDS" | grep -Eq '^[0-9]+$' || [ "$WAIT_TIMEOUT_SECONDS" -le 0 ]; then
  echo "ERROR: --wait-timeout must be a positive integer." >&2
  exit 2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
STATE_DIR="$APPDATA_DIR/state"
LOG_FILE="$STATE_DIR/deployment-proof-$STAMP.log"
SUMMARY_FILE="$STATE_DIR/deployment-proof-$STAMP.summary"
EXPORT_PATH="/krakked/state/krakked-proof-$STAMP.zip"
RESTORE_ROOT="/krakked/state/restore-check-$STAMP"

PASS_COUNT=0
FAIL_COUNT=0
WARN_COUNT=0
RUNTIME_MODE=""
COMPOSE_DISPLAY=""

mkdir -p "$STATE_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

say() {
  printf '\n==> %s\n' "$1"
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf 'PASS: %s\n' "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf 'FAIL: %s\n' "$1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf 'WARN: %s\n' "$1"
}

run_check() {
  local name="$1"
  shift
  say "$name"
  if "$@"; then
    pass "$name"
  else
    fail "$name"
  fi
}

fetch_url() {
  local url="$1"
  if command -v curl >/dev/null 2>&1; then
    curl -fsS "$url"
    return
  fi
  if command -v wget >/dev/null 2>&1; then
    wget -qO- "$url"
    return
  fi
  echo "Neither curl nor wget is available." >&2
  return 1
}

wait_for_endpoint() {
  local label="$1"
  local url="$2"
  local pattern="$3"
  local deadline
  local payload
  deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
  payload=""

  while true; do
    payload="$(fetch_url "$url" 2>&1)" && {
      if printf '%s\n' "$payload" | grep -Eiq "$pattern"; then
        printf '%s\n' "$payload"
        return 0
      fi
    }

    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "Timed out waiting for $label at $url" >&2
      printf '%s\n' "$payload" >&2
      return 1
    fi

    sleep 5
  done
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

image_ref() {
  if [ "$MODE" = "source" ]; then
    printf '%s:%s' "$(env_value KRAKKED_IMAGE krakked)" "$(env_value KRAKKED_IMAGE_TAG unraid-local)"
  else
    printf '%s:%s' "$(env_value KRAKKED_IMAGE ghcr.io/itsrobdude/krakked)" "$(env_value KRAKKED_IMAGE_TAG v0.1.0)"
  fi
}

detect_runtime() {
  if docker compose version >/dev/null 2>&1; then
    RUNTIME_MODE="compose"
    COMPOSE_DISPLAY="docker compose"
    docker compose version
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1; then
    RUNTIME_MODE="compose"
    COMPOSE_DISPLAY="docker-compose"
    docker-compose version
    return 0
  fi
  if command -v docker >/dev/null 2>&1; then
    RUNTIME_MODE="docker"
    docker version
    warn "Docker Compose is unavailable; proof will use plain Docker fallback checks where possible."
    return 0
  fi
  echo "Docker was not found." >&2
  return 1
}

compose_cmd() {
  if [ "$COMPOSE_DISPLAY" = "docker compose" ]; then
    docker compose "$@"
  else
    docker-compose "$@"
  fi
}

container_run() {
  if [ "$RUNTIME_MODE" = "compose" ]; then
    compose_cmd -f "$COMPOSE_FILE" run -T --rm krakked "$@" < /dev/null
    return
  fi

  docker run --rm \
    -e "KRAKKED_ENV=$(env_value KRAKKED_ENV paper)" \
    -e "KRAKKED_CONFIG_DIR=/krakked/config" \
    -e "KRAKKED_DATA_DIR=/krakked/data" \
    -e "KRAKKED_UI_HOST=$(env_value KRAKKED_UI_HOST 0.0.0.0)" \
    -e "KRAKKED_UI_PORT=$(env_value KRAKKED_UI_PORT 8080)" \
    -e "KRAKKED_SECRET_PW=$(env_value KRAKKED_SECRET_PW "")" \
    -e "KRAKEN_API_KEY=$(env_value KRAKEN_API_KEY "")" \
    -e "KRAKEN_API_SECRET=$(env_value KRAKEN_API_SECRET "")" \
    -e "UI_DIST_DIR=/app/ui-dist" \
    -v "$APPDATA_DIR/config:/krakked/config" \
    -v "$APPDATA_DIR/data:/krakked/data" \
    -v "$APPDATA_DIR/state:/krakked/state" \
    "$(image_ref)" "$@"
}

python_in_container() {
  local code="$1"

  if [ "$RUNTIME_MODE" = "compose" ]; then
    compose_cmd -f "$COMPOSE_FILE" run -T --rm --entrypoint python krakked -c "$code" < /dev/null
    return
  fi

  docker run --rm --entrypoint python \
    -e "KRAKKED_ENV=$(env_value KRAKKED_ENV paper)" \
    -e "KRAKKED_CONFIG_DIR=/krakked/config" \
    -e "KRAKKED_DATA_DIR=/krakked/data" \
    -v "$APPDATA_DIR/config:/krakked/config" \
    -v "$APPDATA_DIR/data:/krakked/data" \
    -v "$APPDATA_DIR/state:/krakked/state" \
    "$(image_ref)" -c "$code"
}

container_id() {
  if [ "$RUNTIME_MODE" = "compose" ]; then
    compose_cmd -f "$COMPOSE_FILE" ps -q krakked | tail -n 1
    return
  fi
  docker ps -q --filter name='^/krakked$' | tail -n 1
}

stack_down() {
  if [ "$RUNTIME_MODE" = "compose" ]; then
    compose_cmd -f "$COMPOSE_FILE" down
    return
  fi
  docker stop krakked
}

stack_up() {
  if [ "$RUNTIME_MODE" = "compose" ]; then
    compose_cmd -f "$COMPOSE_FILE" up -d
    return
  fi
  docker start krakked
}

check_repo() {
  for required in scripts/unraid_bootstrap.sh Dockerfile compose.yaml config_examples/config.yaml; do
    [ -e "$required" ] || {
      echo "Missing $required. Run this from the Krakked checkout." >&2
      return 1
    }
  done

  if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    printf 'commit=%s\n' "$(git rev-parse --short HEAD)"
    printf 'branch=%s\n' "$(git branch --show-current 2>/dev/null || true)"
    git status --short
  else
    warn "Not inside a git worktree; commit identity cannot be recorded."
  fi
}

start_stack() {
  local -a args
  args=(--start --mode "$MODE")
  if [ "$RECREATE" = "true" ]; then
    args+=(--recreate)
  fi
  KRAKKED_UNRAID_APPDATA_DIR="$APPDATA_DIR" KRAKKED_UNRAID_COMPOSE_FILE="$COMPOSE_FILE" \
    bash scripts/unraid_bootstrap.sh "${args[@]}"
}

check_container_running() {
  local id status health deadline last_state
  deadline=$((SECONDS + WAIT_TIMEOUT_SECONDS))
  last_state=""

  while true; do
    id="$(container_id)"
    if [ -n "$id" ]; then
      status="$(docker inspect -f '{{.State.Status}}' "$id")"
      health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$id")"
      last_state="container=$id status=$status health=$health"

      if [ "$health" = "healthy" ]; then
        printf '%s\n' "$last_state"
        return 0
      fi
      if [ "$health" = "none" ] && [ "$status" = "running" ]; then
        printf '%s\n' "$last_state"
        warn "Container has no Docker healthcheck in this runtime mode; API health check will be the readiness source."
        return 0
      fi
    else
      last_state="No running krakked container was found."
    fi

    if [ "$SECONDS" -ge "$deadline" ]; then
      printf '%s\n' "$last_state" >&2
      return 1
    fi

    sleep 5
  done
}

check_mounts() {
  [ -f "$APPDATA_DIR/config/config.yaml" ] || return 1
  [ -f "$APPDATA_DIR/config/config.paper.yaml" ] || return 1
  [ -d "$APPDATA_DIR/data" ] || return 1
  [ -d "$APPDATA_DIR/state" ] || return 1
  local probe="$APPDATA_DIR/state/.deployment-proof-write-test-$STAMP"
  printf 'ok\n' > "$probe" && rm -f "$probe"
}

check_health() {
  wait_for_endpoint \
    "health endpoint" \
    "$HOST_URL/api/health" \
    '"status"[[:space:]]*:[[:space:]]*"ok"'
}

check_ui() {
  wait_for_endpoint \
    "UI root" \
    "$HOST_URL/" \
    '(<div id="root"|Krakked)'
}

check_setup_status() {
  wait_for_endpoint \
    "setup status" \
    "$HOST_URL/api/system/setup/status" \
    '"configured"[[:space:]]*:[[:space:]]*true'
}

run_once_check() {
  if [ "$SKIP_RUN_ONCE" = "true" ]; then
    warn "run-once check skipped by request."
    return 0
  fi
  container_run run-once
}

check_db_evidence() {
  python_in_container '
import json
import sqlite3
import sys
from pathlib import Path

db_path = Path("/krakked/state/portfolio.db")
if not db_path.exists():
    print("portfolio.db not found", file=sys.stderr)
    sys.exit(1)

tables = [
    "decisions",
    "execution_plans",
    "execution_orders",
    "execution_results",
    "snapshots",
    "trades",
]

counts = {}
with sqlite3.connect(db_path.as_posix()) as conn:
    for table in tables:
        try:
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            counts[table] = None

print(json.dumps(counts, sort_keys=True))
evidence_total = sum(value for value in counts.values() if isinstance(value, int))
if evidence_total <= 0:
    print("portfolio.db exists but no recognized evidence tables have rows", file=sys.stderr)
    sys.exit(1)
'
}

check_live_gates_closed() {
  python_in_container '
import json
import sys
from pathlib import Path

import yaml

paper_path = Path("/krakked/config/config.paper.yaml")
if not paper_path.exists():
    print("config.paper.yaml not found", file=sys.stderr)
    sys.exit(1)

config = yaml.safe_load(paper_path.read_text()) or {}
execution = config.get("execution") or {}
errors = []
if execution.get("mode") != "paper":
    errors.append("execution.mode is not paper")
if execution.get("allow_live_trading") is not False:
    errors.append("execution.allow_live_trading is not false")

print(json.dumps({"execution": execution}, sort_keys=True))
if errors:
    print("; ".join(errors), file=sys.stderr)
    sys.exit(1)
'
}

restart_persistence_check() {
  local db_path="$APPDATA_DIR/state/portfolio.db"
  [ -f "$db_path" ] || return 1
  local before
  before="$(wc -c < "$db_path")"
  stack_down || return 1
  stack_up || return 1
  sleep 5
  check_container_running || return 1
  [ -f "$db_path" ] || return 1
  local after
  after="$(wc -c < "$db_path")"
  printf 'portfolio.db bytes before=%s after=%s\n' "$before" "$after"
  [ "$after" -gt 0 ]
}

export_check() {
  container_run export-install \
    --config-dir /krakked/config \
    --db-path /krakked/state/portfolio.db \
    --data-dir /krakked/data \
    --include-data \
    --output "$EXPORT_PATH"
}

restore_check() {
  if [ "$SKIP_RESTORE" = "true" ]; then
    warn "restore check skipped by request."
    return 0
  fi
  container_run import-install \
    --input "$EXPORT_PATH" \
    --config-dir "$RESTORE_ROOT/config" \
    --db-path "$RESTORE_ROOT/portfolio.db" \
    --data-dir "$RESTORE_ROOT/data"
}

final_status_check() {
  check_container_running || return 1
  check_health || return 1
}

write_summary() {
  local result="PASS"
  local rc=0
  if [ "$FAIL_COUNT" -gt 0 ]; then
    result="FAIL"
    rc=1
  fi

  {
    printf 'DEPLOYMENT_PROOF_RESULT=%s\n' "$result"
    printf 'pass=%s\n' "$PASS_COUNT"
    printf 'fail=%s\n' "$FAIL_COUNT"
    printf 'warn=%s\n' "$WARN_COUNT"
    printf 'log=%s\n' "$LOG_FILE"
    printf 'summary=%s\n' "$SUMMARY_FILE"
    printf 'host_url=%s\n' "$HOST_URL"
    printf 'appdata_dir=%s\n' "$APPDATA_DIR"
    printf 'compose_file=%s\n' "$COMPOSE_FILE"
    printf 'mode=%s\n' "$MODE"
    if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
      printf 'commit=%s\n' "$(git rev-parse --short HEAD)"
    fi
  } | tee "$SUMMARY_FILE"

  return "$rc"
}

say "Krakked Unraid deployment proof"
printf 'host_url=%s\n' "$HOST_URL"
printf 'appdata_dir=%s\n' "$APPDATA_DIR"
printf 'compose_file=%s\n' "$COMPOSE_FILE"
printf 'mode=%s recreate=%s\n' "$MODE" "$RECREATE"
printf 'wait_timeout_seconds=%s\n' "$WAIT_TIMEOUT_SECONDS"
printf 'log=%s\n' "$LOG_FILE"

run_check "Repo checkout and commit identity" check_repo
run_check "Docker runtime available" detect_runtime
run_check "Bootstrap and start paper stack" start_stack
run_check "Container running and healthy" check_container_running
run_check "Persistent appdata mounts exist and are writable" check_mounts
run_check "Health endpoint returns ok" check_health
run_check "UI root is reachable" check_ui
run_check "Setup status is readable" check_setup_status
run_check "Forced-safe paper run-once completes" run_once_check
run_check "Synthetic portfolio store has persisted evidence" check_db_evidence
run_check "Paper live gates are closed" check_live_gates_closed
run_check "Restart keeps persisted state" restart_persistence_check
run_check "Full install export succeeds" export_check
run_check "Restore round-trip into scratch succeeds" restore_check
run_check "Final container status and health endpoint" final_status_check

say "Summary"
if write_summary; then
  exit 0
fi
exit 1
