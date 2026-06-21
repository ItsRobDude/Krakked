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
PIN_IMAGE=""
PIN_IMAGE_TAG=""
EXPECTED_BUILD_GIT_SHA=""

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
  --image IMAGE          Pin KRAKKED_IMAGE in .env before starting the stack
  --image-tag TAG        Pin KRAKKED_IMAGE_TAG in .env before starting the stack
  --expected-build-git-sha SHA
                          Require the app to report this build SHA
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
    --image)
      shift
      PIN_IMAGE="${1:-}"
      ;;
    --image-tag)
      shift
      PIN_IMAGE_TAG="${1:-}"
      ;;
    --expected-build-git-sha)
      shift
      EXPECTED_BUILD_GIT_SHA="${1:-}"
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
HEALTH_PROVENANCE_JSON=""
SYSTEM_HEALTH_PROVENANCE_JSON=""
ACTUAL_IMAGE_NAME=""
ACTUAL_IMAGE_TAG=""
ACTUAL_BUILD_GIT_SHA=""
ACTUAL_RUNTIME_SOURCE=""
EXPECTED_IMAGE_NAME=""
EXPECTED_IMAGE_TAG=""
EXPECTED_BUILD_SHA_REPORTED=""
EXPECTED_RUNTIME_SOURCE_REPORTED=""
DEPLOYMENT_DRIFT_DETECTED=""
DEPLOYMENT_DRIFT_REASON=""
IMAGE_REF_REPORTED=""
IMAGE_ID_REPORTED=""
IMAGE_REPO_DIGESTS_REPORTED=""
COMPOSE_PERSISTENCE_RESULT=""
COMPOSE_VERSION_REPORTED=""
COMPOSE_RUNTIME_PATH_REPORTED=""
COMPOSE_FLASH_PATH_REPORTED=""
COMPOSE_GO_FILE_REPORTED=""
COMPOSE_RUNTIME_SHA256_REPORTED=""
COMPOSE_FLASH_SHA256_REPORTED=""
COMPOSE_HASH_MATCH_REPORTED=""
COMPOSE_GO_BLOCK_PRESENT_REPORTED=""

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

summary_value_from_text() {
  local text="$1"
  local key="$2"
  printf '%s\n' "$text" | awk -F= -v key="$key" '
    $1 == key {
      print substr($0, index($0, "=") + 1)
      found = 1
      exit
    }
    END { if (!found) exit 1 }
  ' || true
}

set_env_key() {
  local key="$1"
  local value="$2"
  local tmp=".env.tmp.$$"

  if [ ! -f .env ]; then
    printf '%s=%s\n' "$key" "$value" > .env
    return
  fi

  awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    $0 ~ "^" key "=" { print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' .env > "$tmp" && mv "$tmp" .env
}

apply_pinned_image_inputs() {
  if [ -n "$PIN_IMAGE" ]; then
    set_env_key "KRAKKED_IMAGE" "$PIN_IMAGE"
    set_env_key "KRAKKED_EXPECTED_IMAGE" "$PIN_IMAGE"
  fi
  if [ -n "$PIN_IMAGE_TAG" ]; then
    set_env_key "KRAKKED_IMAGE_TAG" "$PIN_IMAGE_TAG"
    set_env_key "KRAKKED_EXPECTED_IMAGE_TAG" "$PIN_IMAGE_TAG"
  fi
  if [ -n "$EXPECTED_BUILD_GIT_SHA" ]; then
    set_env_key "KRAKKED_EXPECTED_BUILD_GIT_SHA" "$EXPECTED_BUILD_GIT_SHA"
  fi
  if [ "$MODE" = "image" ]; then
    set_env_key "KRAKKED_RUNTIME_SOURCE" "image"
    set_env_key "KRAKKED_EXPECTED_RUNTIME_SOURCE" "image"
    if [ -z "$EXPECTED_BUILD_GIT_SHA" ]; then
      set_env_key "KRAKKED_EXPECTED_BUILD_GIT_SHA" ""
    fi
  fi
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
    -e "KRAKKED_RUNTIME_IMAGE=$(env_value KRAKKED_IMAGE krakked)" \
    -e "KRAKKED_RUNTIME_IMAGE_TAG=$(env_value KRAKKED_IMAGE_TAG unraid-local)" \
    -e "KRAKKED_RUNTIME_IMAGE_DIGEST=$(env_value KRAKKED_IMAGE_DIGEST "")" \
    -e "KRAKKED_RUNTIME_SOURCE=$(env_value KRAKKED_RUNTIME_SOURCE "$MODE")" \
    -e "KRAKKED_EXPECTED_IMAGE=$(env_value KRAKKED_EXPECTED_IMAGE "")" \
    -e "KRAKKED_EXPECTED_IMAGE_TAG=$(env_value KRAKKED_EXPECTED_IMAGE_TAG "")" \
    -e "KRAKKED_EXPECTED_BUILD_GIT_SHA=$(env_value KRAKKED_EXPECTED_BUILD_GIT_SHA "")" \
    -e "KRAKKED_EXPECTED_RUNTIME_SOURCE=$(env_value KRAKKED_EXPECTED_RUNTIME_SOURCE "")" \
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
    -e "KRAKKED_RUNTIME_IMAGE=$(env_value KRAKKED_IMAGE krakked)" \
    -e "KRAKKED_RUNTIME_IMAGE_TAG=$(env_value KRAKKED_IMAGE_TAG unraid-local)" \
    -e "KRAKKED_RUNTIME_IMAGE_DIGEST=$(env_value KRAKKED_IMAGE_DIGEST "")" \
    -e "KRAKKED_RUNTIME_SOURCE=$(env_value KRAKKED_RUNTIME_SOURCE "$MODE")" \
    -e "KRAKKED_EXPECTED_IMAGE=$(env_value KRAKKED_EXPECTED_IMAGE "")" \
    -e "KRAKKED_EXPECTED_IMAGE_TAG=$(env_value KRAKKED_EXPECTED_IMAGE_TAG "")" \
    -e "KRAKKED_EXPECTED_BUILD_GIT_SHA=$(env_value KRAKKED_EXPECTED_BUILD_GIT_SHA "")" \
    -e "KRAKKED_EXPECTED_RUNTIME_SOURCE=$(env_value KRAKKED_EXPECTED_RUNTIME_SOURCE "")" \
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
      if [ -n "$id" ] && [ "$status" = "running" ] && fetch_url "$HOST_URL/api/health" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"ok"'; then
        printf '%s\n' "$last_state"
        warn "Docker healthcheck is not healthy, but /api/health is OK; treating app HTTP health as authoritative for this Unraid proof."
        return 0
      fi
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

json_string_field() {
  local payload="$1"
  local field="$2"
  printf '%s' "$payload" | sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p"
}

json_bool_field() {
  local payload="$1"
  local field="$2"
  printf '%s' "$payload" | sed -n "s/.*\"$field\"[[:space:]]*:[[:space:]]*\(true\|false\).*/\1/p"
}

record_image_metadata() {
  local ref
  local digests
  ref="$(image_ref)"
  IMAGE_REF_REPORTED="$ref"
  IMAGE_ID_REPORTED="$(docker image inspect -f '{{.Id}}' "$ref" 2>/dev/null || true)"
  digests="$(docker image inspect -f '{{range .RepoDigests}}{{println .}}{{end}}' "$ref" 2>/dev/null || true)"
  IMAGE_REPO_DIGESTS_REPORTED="$(printf '%s\n' "$digests" | awk 'NF { values = values sep $0; sep = "," } END { print values }')"
  printf 'image_ref=%s\n' "$IMAGE_REF_REPORTED"
  printf 'image_id=%s\n' "${IMAGE_ID_REPORTED:-unknown}"
  printf 'image_repo_digests=%s\n' "${IMAGE_REPO_DIGESTS_REPORTED:-none}"
}

check_compose_persistence() {
  local output
  local rc=0
  if [ ! -f scripts/unraid_compose_persistence.sh ]; then
    echo "Missing scripts/unraid_compose_persistence.sh" >&2
    COMPOSE_PERSISTENCE_RESULT="missing_helper"
    return 1
  fi

  output="$(bash scripts/unraid_compose_persistence.sh check 2>&1)" || rc=$?
  printf '%s\n' "$output"

  COMPOSE_PERSISTENCE_RESULT="$(summary_value_from_text "$output" compose_persistence_result)"
  COMPOSE_VERSION_REPORTED="$(summary_value_from_text "$output" compose_version)"
  COMPOSE_RUNTIME_PATH_REPORTED="$(summary_value_from_text "$output" compose_runtime_path)"
  COMPOSE_FLASH_PATH_REPORTED="$(summary_value_from_text "$output" compose_flash_path)"
  COMPOSE_GO_FILE_REPORTED="$(summary_value_from_text "$output" compose_go_file)"
  COMPOSE_RUNTIME_SHA256_REPORTED="$(summary_value_from_text "$output" compose_runtime_sha256)"
  COMPOSE_FLASH_SHA256_REPORTED="$(summary_value_from_text "$output" compose_flash_sha256)"
  COMPOSE_HASH_MATCH_REPORTED="$(summary_value_from_text "$output" compose_hash_match)"
  COMPOSE_GO_BLOCK_PRESENT_REPORTED="$(summary_value_from_text "$output" compose_go_block_present)"

  if [ "$rc" -ne 0 ]; then
    return "$rc"
  fi
  if [ "$COMPOSE_PERSISTENCE_RESULT" != "PASS" ]; then
    echo "Docker Compose persistence check did not pass: ${COMPOSE_PERSISTENCE_RESULT:-unknown}" >&2
    return 1
  fi
}

check_runtime_provenance() {
  local field
  HEALTH_PROVENANCE_JSON="$(fetch_url "$HOST_URL/api/health" | tr -d '\n')" || return 1
  SYSTEM_HEALTH_PROVENANCE_JSON="$(fetch_url "$HOST_URL/api/system/health" | tr -d '\n')" || return 1

  printf 'health_payload=%s\n' "$HEALTH_PROVENANCE_JSON"
  printf 'system_health_payload=%s\n' "$SYSTEM_HEALTH_PROVENANCE_JSON"

  for field in app_version build_git_sha build_git_ref image_name image_tag image_digest runtime_source expected_image_name expected_image_tag expected_build_git_sha expected_runtime_source deployment_drift_detected deployment_drift_reason; do
    if ! printf '%s\n' "$HEALTH_PROVENANCE_JSON" | grep -q "\"$field\""; then
      echo "Missing $field in /api/health" >&2
      return 1
    fi
    if ! printf '%s\n' "$SYSTEM_HEALTH_PROVENANCE_JSON" | grep -q "\"$field\""; then
      echo "Missing $field in /api/system/health" >&2
      return 1
    fi
  done

  ACTUAL_IMAGE_NAME="$(json_string_field "$HEALTH_PROVENANCE_JSON" image_name)"
  ACTUAL_IMAGE_TAG="$(json_string_field "$HEALTH_PROVENANCE_JSON" image_tag)"
  ACTUAL_BUILD_GIT_SHA="$(json_string_field "$HEALTH_PROVENANCE_JSON" build_git_sha)"
  ACTUAL_RUNTIME_SOURCE="$(json_string_field "$HEALTH_PROVENANCE_JSON" runtime_source)"
  EXPECTED_IMAGE_NAME="$(json_string_field "$HEALTH_PROVENANCE_JSON" expected_image_name)"
  EXPECTED_IMAGE_TAG="$(json_string_field "$HEALTH_PROVENANCE_JSON" expected_image_tag)"
  EXPECTED_BUILD_SHA_REPORTED="$(json_string_field "$HEALTH_PROVENANCE_JSON" expected_build_git_sha)"
  EXPECTED_RUNTIME_SOURCE_REPORTED="$(json_string_field "$HEALTH_PROVENANCE_JSON" expected_runtime_source)"
  DEPLOYMENT_DRIFT_DETECTED="$(json_bool_field "$HEALTH_PROVENANCE_JSON" deployment_drift_detected)"
  DEPLOYMENT_DRIFT_REASON="$(json_string_field "$HEALTH_PROVENANCE_JSON" deployment_drift_reason)"

  printf 'actual_image_name=%s\n' "$ACTUAL_IMAGE_NAME"
  printf 'actual_image_tag=%s\n' "$ACTUAL_IMAGE_TAG"
  printf 'actual_build_git_sha=%s\n' "$ACTUAL_BUILD_GIT_SHA"
  printf 'actual_runtime_source=%s\n' "$ACTUAL_RUNTIME_SOURCE"
  printf 'expected_image_name=%s\n' "$EXPECTED_IMAGE_NAME"
  printf 'expected_image_tag=%s\n' "$EXPECTED_IMAGE_TAG"
  printf 'expected_build_git_sha=%s\n' "$EXPECTED_BUILD_SHA_REPORTED"
  printf 'expected_runtime_source=%s\n' "$EXPECTED_RUNTIME_SOURCE_REPORTED"
  printf 'deployment_drift_detected=%s\n' "$DEPLOYMENT_DRIFT_DETECTED"
  printf 'deployment_drift_reason=%s\n' "${DEPLOYMENT_DRIFT_REASON:-none}"

  if [ "$MODE" = "image" ] && [ "$ACTUAL_RUNTIME_SOURCE" != "image" ]; then
    echo "Image-mode proof requires runtime_source=image; got ${ACTUAL_RUNTIME_SOURCE:-unknown}" >&2
    return 1
  fi
  if [ "$DEPLOYMENT_DRIFT_DETECTED" = "true" ]; then
    echo "Deployment provenance drift detected: ${DEPLOYMENT_DRIFT_REASON:-unknown}" >&2
    return 1
  fi
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
    printf 'skip_run_once=%s\n' "$SKIP_RUN_ONCE"
    printf 'skip_restore=%s\n' "$SKIP_RESTORE"
    printf 'actual_image_name=%s\n' "$ACTUAL_IMAGE_NAME"
    printf 'actual_image_tag=%s\n' "$ACTUAL_IMAGE_TAG"
    printf 'actual_build_git_sha=%s\n' "$ACTUAL_BUILD_GIT_SHA"
    printf 'actual_runtime_source=%s\n' "$ACTUAL_RUNTIME_SOURCE"
    printf 'expected_image_name=%s\n' "$EXPECTED_IMAGE_NAME"
    printf 'expected_image_tag=%s\n' "$EXPECTED_IMAGE_TAG"
    printf 'expected_build_git_sha=%s\n' "$EXPECTED_BUILD_SHA_REPORTED"
    printf 'expected_runtime_source=%s\n' "$EXPECTED_RUNTIME_SOURCE_REPORTED"
    printf 'deployment_drift_detected=%s\n' "$DEPLOYMENT_DRIFT_DETECTED"
    printf 'deployment_drift_reason=%s\n' "${DEPLOYMENT_DRIFT_REASON:-}"
    printf 'image_ref=%s\n' "$IMAGE_REF_REPORTED"
    printf 'image_id=%s\n' "$IMAGE_ID_REPORTED"
    printf 'image_repo_digests=%s\n' "$IMAGE_REPO_DIGESTS_REPORTED"
    printf 'compose_persistence_result=%s\n' "$COMPOSE_PERSISTENCE_RESULT"
    printf 'compose_version=%s\n' "$COMPOSE_VERSION_REPORTED"
    printf 'compose_runtime_path=%s\n' "$COMPOSE_RUNTIME_PATH_REPORTED"
    printf 'compose_flash_path=%s\n' "$COMPOSE_FLASH_PATH_REPORTED"
    printf 'compose_go_file=%s\n' "$COMPOSE_GO_FILE_REPORTED"
    printf 'compose_runtime_sha256=%s\n' "$COMPOSE_RUNTIME_SHA256_REPORTED"
    printf 'compose_flash_sha256=%s\n' "$COMPOSE_FLASH_SHA256_REPORTED"
    printf 'compose_hash_match=%s\n' "$COMPOSE_HASH_MATCH_REPORTED"
    printf 'compose_go_block_present=%s\n' "$COMPOSE_GO_BLOCK_PRESENT_REPORTED"
    if [ -n "$HEALTH_PROVENANCE_JSON" ]; then
      printf 'health_payload=%s\n' "$HEALTH_PROVENANCE_JSON"
    fi
    if [ -n "$SYSTEM_HEALTH_PROVENANCE_JSON" ]; then
      printf 'system_health_payload=%s\n' "$SYSTEM_HEALTH_PROVENANCE_JSON"
    fi
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
run_check "Docker Compose reboot persistence is configured" check_compose_persistence
run_check "Pinned image inputs are applied" apply_pinned_image_inputs
run_check "Bootstrap and start paper stack" start_stack
run_check "Container running and healthy" check_container_running
run_check "Persistent appdata mounts exist and are writable" check_mounts
run_check "Health endpoint returns ok" check_health
run_check "Runtime provenance is reported" check_runtime_provenance
run_check "Docker image metadata is recorded" record_image_metadata
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
