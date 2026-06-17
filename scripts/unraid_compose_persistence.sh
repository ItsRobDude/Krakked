#!/usr/bin/env bash
set -euo pipefail

RUNTIME_PATH="${KRAKKED_UNRAID_COMPOSE_RUNTIME_PATH:-/usr/local/lib/docker/cli-plugins/docker-compose}"
FLASH_PATH="${KRAKKED_UNRAID_COMPOSE_FLASH_PATH:-/boot/config/plugins/docker-compose-cli/docker-compose-linux-x86_64}"
GO_FILE="${KRAKKED_UNRAID_GO_FILE:-/boot/config/go}"
BEGIN_MARKER="# BEGIN krakked docker compose cli persistence"
END_MARKER="# END krakked docker compose cli persistence"

usage() {
  cat <<'USAGE'
Krakked Unraid Docker Compose CLI persistence helper

Usage:
  bash scripts/unraid_compose_persistence.sh check
  bash scripts/unraid_compose_persistence.sh install
  bash scripts/unraid_compose_persistence.sh repair-runtime

Commands:
  check           Verify runtime binary, flash copy, matching hashes, boot block,
                  and docker compose availability.
  install         Copy the current runtime Compose plugin to flash and install the
                  marked /boot/config/go restore block.
  repair-runtime  Restore the runtime Compose plugin from the flash-backed copy.

Environment overrides:
  KRAKKED_UNRAID_COMPOSE_RUNTIME_PATH=/usr/local/lib/docker/cli-plugins/docker-compose
  KRAKKED_UNRAID_COMPOSE_FLASH_PATH=/boot/config/plugins/docker-compose-cli/docker-compose-linux-x86_64
  KRAKKED_UNRAID_GO_FILE=/boot/config/go
USAGE
}

bool_file_present() {
  if [ -f "$1" ]; then
    printf 'true'
  else
    printf 'false'
  fi
}

bool_file_executable() {
  if [ -x "$1" ]; then
    printf 'true'
  else
    printf 'false'
  fi
}

sha256_file() {
  if [ ! -f "$1" ]; then
    printf ''
    return
  fi
  sha256sum "$1" | awk '{ print $1 }'
}

go_block_present() {
  if [ -f "$GO_FILE" ] && grep -qF "$BEGIN_MARKER" "$GO_FILE" && grep -qF "$END_MARKER" "$GO_FILE"; then
    printf 'true'
  else
    printf 'false'
  fi
}

compose_version() {
  docker compose version 2>/dev/null || true
}

emit_status() {
  local result="$1"
  local runtime_present
  local flash_present
  local runtime_executable
  local go_present
  local runtime_sha
  local flash_sha
  local hash_match="false"
  local version

  runtime_present="$(bool_file_present "$RUNTIME_PATH")"
  flash_present="$(bool_file_present "$FLASH_PATH")"
  runtime_executable="$(bool_file_executable "$RUNTIME_PATH")"
  go_present="$(go_block_present)"
  runtime_sha="$(sha256_file "$RUNTIME_PATH")"
  flash_sha="$(sha256_file "$FLASH_PATH")"
  version="$(compose_version | tr -d '\r')"

  if [ -n "$runtime_sha" ] && [ -n "$flash_sha" ] && [ "$runtime_sha" = "$flash_sha" ]; then
    hash_match="true"
  fi

  printf 'compose_persistence_result=%s\n' "$result"
  printf 'compose_version=%s\n' "${version:-unknown}"
  printf 'compose_runtime_path=%s\n' "$RUNTIME_PATH"
  printf 'compose_flash_path=%s\n' "$FLASH_PATH"
  printf 'compose_go_file=%s\n' "$GO_FILE"
  printf 'compose_runtime_present=%s\n' "$runtime_present"
  printf 'compose_flash_present=%s\n' "$flash_present"
  printf 'compose_runtime_executable=%s\n' "$runtime_executable"
  printf 'compose_go_block_present=%s\n' "$go_present"
  printf 'compose_runtime_sha256=%s\n' "${runtime_sha:-unknown}"
  printf 'compose_flash_sha256=%s\n' "${flash_sha:-unknown}"
  printf 'compose_hash_match=%s\n' "$hash_match"
}

check_persistence() {
  local failed="false"
  local runtime_sha
  local flash_sha

  if [ ! -f "$RUNTIME_PATH" ]; then
    echo "Runtime Docker Compose plugin is missing at $RUNTIME_PATH" >&2
    failed="true"
  fi
  if [ ! -x "$RUNTIME_PATH" ]; then
    echo "Runtime Docker Compose plugin is not executable at $RUNTIME_PATH" >&2
    failed="true"
  fi
  if [ ! -f "$FLASH_PATH" ]; then
    echo "Flash-backed Docker Compose plugin copy is missing at $FLASH_PATH" >&2
    failed="true"
  fi
  if [ "$(go_block_present)" != "true" ]; then
    echo "Boot restore block is missing from $GO_FILE" >&2
    failed="true"
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "docker compose is not available through the Docker CLI plugin path" >&2
    failed="true"
  fi

  runtime_sha="$(sha256_file "$RUNTIME_PATH")"
  flash_sha="$(sha256_file "$FLASH_PATH")"
  if [ -n "$runtime_sha" ] && [ -n "$flash_sha" ] && [ "$runtime_sha" != "$flash_sha" ]; then
    echo "Runtime and flash-backed Docker Compose plugin hashes differ" >&2
    failed="true"
  fi

  if [ "$failed" = "true" ]; then
    emit_status "FAIL"
    return 1
  fi

  emit_status "PASS"
}

install_go_block() {
  local tmp
  local backup
  local quoted_flash_path
  local quoted_runtime_dir
  local quoted_runtime_path

  mkdir -p "$(dirname "$GO_FILE")"
  if [ ! -f "$GO_FILE" ]; then
    cat > "$GO_FILE" <<'EOF'
#!/bin/bash
# Start the Management Utility
/usr/local/sbin/emhttp
EOF
  fi

  backup="${GO_FILE}.krakked-compose-$(date +%Y%m%d-%H%M%S).bak"
  cp -p "$GO_FILE" "$backup"

  tmp="$(mktemp)"
  awk -v begin="$BEGIN_MARKER" -v end="$END_MARKER" '
    $0 == begin { skip = 1; next }
    $0 == end { skip = 0; next }
    !skip { print }
  ' "$GO_FILE" > "$tmp"

  printf -v quoted_flash_path '%q' "$FLASH_PATH"
  printf -v quoted_runtime_dir '%q' "$(dirname "$RUNTIME_PATH")"
  printf -v quoted_runtime_path '%q' "$RUNTIME_PATH"

  cat >> "$tmp" <<EOF
$BEGIN_MARKER
# Restore the Docker Compose CLI plugin from the Unraid flash drive on boot.
if [ -f $quoted_flash_path ]; then
  mkdir -p $quoted_runtime_dir
  cp -f $quoted_flash_path $quoted_runtime_path
  chmod 0755 $quoted_runtime_path
fi
$END_MARKER
EOF

  mv "$tmp" "$GO_FILE"
  chmod 0600 "$GO_FILE"
  printf 'go_backup=%s\n' "$backup"
}

install_persistence() {
  if [ ! -f "$RUNTIME_PATH" ]; then
    echo "Cannot install persistence because runtime Compose plugin is missing at $RUNTIME_PATH" >&2
    emit_status "FAIL"
    return 1
  fi

  mkdir -p "$(dirname "$FLASH_PATH")"
  cp -f "$RUNTIME_PATH" "$FLASH_PATH"
  chmod 0600 "$FLASH_PATH"
  install_go_block
  check_persistence
}

repair_runtime() {
  if [ ! -f "$FLASH_PATH" ]; then
    echo "Cannot repair runtime Compose plugin because flash copy is missing at $FLASH_PATH" >&2
    emit_status "FAIL"
    return 1
  fi

  mkdir -p "$(dirname "$RUNTIME_PATH")"
  cp -f "$FLASH_PATH" "$RUNTIME_PATH"
  chmod 0755 "$RUNTIME_PATH"
  check_persistence
}

COMMAND="${1:-check}"
case "$COMMAND" in
  check)
    check_persistence
    ;;
  install)
    install_persistence
    ;;
  repair-runtime)
    repair_runtime
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    echo "ERROR: Unknown command: $COMMAND" >&2
    usage >&2
    exit 2
    ;;
esac
