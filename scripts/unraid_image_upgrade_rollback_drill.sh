#!/usr/bin/env bash
set -uo pipefail

APPDATA_DIR="${KRAKKED_UNRAID_APPDATA_DIR:-/mnt/user/appdata/krakked}"
COMPOSE_FILE="${KRAKKED_UNRAID_COMPOSE_FILE:-compose.unraid.yaml}"
HOST_URL="${KRAKKED_PROOF_HOST_URL:-http://127.0.0.1:8088}"
IMAGE="ghcr.io/itsrobdude/krakked"
FROM_TAG=""
TO_TAG=""
FROM_SHA=""
TO_SHA=""

usage() {
  cat <<'USAGE'
Krakked pinned-image upgrade/rollback drill

Usage:
  bash scripts/unraid_image_upgrade_rollback_drill.sh --from-tag TAG --to-tag TAG [options]

Options:
  --image IMAGE              Image repository, default ghcr.io/itsrobdude/krakked
  --from-tag TAG             Starting pinned image tag
  --to-tag TAG               Upgrade pinned image tag
  --from-sha SHA             Optional expected build SHA for --from-tag
  --to-sha SHA               Optional expected build SHA for --to-tag
  --host-url URL             URL to test, default http://127.0.0.1:8088
  --appdata-dir PATH         Appdata root, default /mnt/user/appdata/krakked
  --compose-file PATH        Compose file, default compose.unraid.yaml
  -h, --help                 Show this help
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --image)
      shift
      IMAGE="${1:-}"
      ;;
    --from-tag)
      shift
      FROM_TAG="${1:-}"
      ;;
    --to-tag)
      shift
      TO_TAG="${1:-}"
      ;;
    --from-sha)
      shift
      FROM_SHA="${1:-}"
      ;;
    --to-sha)
      shift
      TO_SHA="${1:-}"
      ;;
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

if [ -z "$IMAGE" ] || [ -z "$FROM_TAG" ] || [ -z "$TO_TAG" ]; then
  echo "ERROR: --image, --from-tag, and --to-tag must be non-empty." >&2
  usage
  exit 2
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
STATE_DIR="$APPDATA_DIR/state"
LOG_FILE="$STATE_DIR/image-upgrade-rollback-$STAMP.log"
SUMMARY_FILE="$STATE_DIR/image-upgrade-rollback-$STAMP.summary"
DETAIL_FILE="$STATE_DIR/image-upgrade-rollback-$STAMP.details"
FAIL_COUNT=0

mkdir -p "$STATE_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

say() {
  printf '\n==> %s\n' "$1"
}

fail_phase() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf 'FAIL: %s\n' "$1"
}

summary_value() {
  local path="$1"
  local key="$2"
  awk -F= -v key="$key" '$1 == key { print substr($0, index($0, "=") + 1); exit }' "$path"
}

db_size() {
  local db_path="$APPDATA_DIR/state/portfolio.db"
  if [ -f "$db_path" ]; then
    wc -c < "$db_path"
  else
    printf 'missing'
  fi
}

run_phase() {
  local phase="$1"
  local tag="$2"
  local expected_sha="$3"
  local before_size
  local after_size
  local rc
  local latest_summary
  local -a proof_args

  say "$phase: deploy $IMAGE:$tag"
  before_size="$(db_size)"
  proof_args=(
    scripts/unraid_deployment_proof.sh
    --mode image
    --image "$IMAGE"
    --image-tag "$tag"
    --host-url "$HOST_URL"
    --appdata-dir "$APPDATA_DIR"
    --compose-file "$COMPOSE_FILE"
  )
  if [ -n "$expected_sha" ]; then
    proof_args+=(--expected-build-git-sha "$expected_sha")
  fi

  bash "${proof_args[@]}"
  rc=$?
  after_size="$(db_size)"
  latest_summary="$(ls -t "$STATE_DIR"/deployment-proof-*.summary 2>/dev/null | head -1 || true)"

  {
    printf '%s_rc=%s\n' "$phase" "$rc"
    printf '%s_summary=%s\n' "$phase" "$latest_summary"
    printf '%s_portfolio_db_path=%s\n' "$phase" "$APPDATA_DIR/state/portfolio.db"
    printf '%s_portfolio_db_size_before=%s\n' "$phase" "$before_size"
    printf '%s_portfolio_db_size_after=%s\n' "$phase" "$after_size"
  } >> "$DETAIL_FILE"

  if [ "$rc" -ne 0 ]; then
    fail_phase "$phase proof exited with $rc"
    return
  fi
  if [ -z "$latest_summary" ] || [ ! -f "$latest_summary" ]; then
    fail_phase "$phase did not produce a deployment proof summary"
    return
  fi
  if [ "$(summary_value "$latest_summary" DEPLOYMENT_PROOF_RESULT)" != "PASS" ]; then
    fail_phase "$phase deployment proof did not pass"
  fi
  if [ "$(summary_value "$latest_summary" fail)" != "0" ]; then
    fail_phase "$phase deployment proof reported failures"
  fi
  if [ "$(summary_value "$latest_summary" skip_run_once)" != "false" ]; then
    fail_phase "$phase skipped run-once"
  fi
  if [ "$(summary_value "$latest_summary" skip_restore)" != "false" ]; then
    fail_phase "$phase skipped restore"
  fi
  if [ "$(summary_value "$latest_summary" actual_runtime_source)" != "image" ]; then
    fail_phase "$phase did not run from image provenance"
  fi
  if [ "$(summary_value "$latest_summary" actual_image_tag)" != "$tag" ]; then
    fail_phase "$phase reported the wrong image tag"
  fi
  if [ "$after_size" = "missing" ] || [ "$after_size" = "0" ]; then
    fail_phase "$phase portfolio DB was not preserved"
  fi
}

say "Krakked pinned-image upgrade/rollback drill"
printf 'image=%s\n' "$IMAGE"
printf 'from_tag=%s\n' "$FROM_TAG"
printf 'to_tag=%s\n' "$TO_TAG"
printf 'host_url=%s\n' "$HOST_URL"
printf 'appdata_dir=%s\n' "$APPDATA_DIR"
printf 'compose_file=%s\n' "$COMPOSE_FILE"
printf 'log=%s\n' "$LOG_FILE"

: > "$DETAIL_FILE"
run_phase "phase_initial" "$FROM_TAG" "$FROM_SHA"
run_phase "phase_upgrade" "$TO_TAG" "$TO_SHA"
run_phase "phase_rollback" "$FROM_TAG" "$FROM_SHA"

result="PASS"
if [ "$FAIL_COUNT" -gt 0 ]; then
  result="FAIL"
fi

{
  printf 'IMAGE_UPGRADE_ROLLBACK_RESULT=%s\n' "$result"
  printf 'fail=%s\n' "$FAIL_COUNT"
  printf 'image=%s\n' "$IMAGE"
  printf 'from_tag=%s\n' "$FROM_TAG"
  printf 'to_tag=%s\n' "$TO_TAG"
  printf 'host_url=%s\n' "$HOST_URL"
  printf 'appdata_dir=%s\n' "$APPDATA_DIR"
  printf 'compose_file=%s\n' "$COMPOSE_FILE"
  printf 'log=%s\n' "$LOG_FILE"
  printf 'summary=%s\n' "$SUMMARY_FILE"
  cat "$DETAIL_FILE"
} | tee "$SUMMARY_FILE"

if [ "$FAIL_COUNT" -gt 0 ]; then
  exit 1
fi
exit 0
