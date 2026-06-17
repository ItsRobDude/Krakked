from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _script_text(name: str) -> str:
    return (REPO_ROOT / "scripts" / name).read_text(encoding="utf-8")


def test_unraid_deployment_proof_enforces_image_provenance_contract():
    script = _script_text("unraid_deployment_proof.sh")

    assert "--expected-build-git-sha SHA" in script
    assert 'set_env_key "KRAKKED_EXPECTED_IMAGE"' in script
    assert 'set_env_key "KRAKKED_EXPECTED_IMAGE_TAG"' in script
    assert 'set_env_key "KRAKKED_EXPECTED_RUNTIME_SOURCE" "image"' in script
    assert (
        'if [ "$MODE" = "image" ] && [ "$ACTUAL_RUNTIME_SOURCE" != "image" ]' in script
    )
    assert 'if [ "$DEPLOYMENT_DRIFT_DETECTED" = "true" ]' in script
    assert "Deployment provenance drift detected" in script


def test_unraid_deployment_proof_records_image_identity_in_summary():
    script = _script_text("unraid_deployment_proof.sh")

    for field in (
        "actual_image_name",
        "actual_image_tag",
        "expected_image_name",
        "expected_image_tag",
        "deployment_drift_detected",
        "image_ref",
        "image_id",
        "image_repo_digests",
        "compose_persistence_result",
        "compose_version",
        "compose_runtime_path",
        "compose_flash_path",
        "compose_runtime_sha256",
        "compose_flash_sha256",
        "compose_hash_match",
        "compose_go_block_present",
    ):
        assert f"printf '{field}=%s\\n'" in script


def test_unraid_deployment_proof_requires_compose_reboot_persistence():
    script = _script_text("unraid_deployment_proof.sh")

    assert "Docker Compose reboot persistence is configured" in script
    assert "scripts/unraid_compose_persistence.sh check" in script
    assert "COMPOSE_PERSISTENCE_RESULT" in script
    assert "Docker Compose persistence check did not pass" in script


def test_unraid_compose_persistence_helper_is_idempotent_and_marked():
    script = _script_text("unraid_compose_persistence.sh")

    assert "BEGIN krakked docker compose cli persistence" in script
    assert "END krakked docker compose cli persistence" in script
    assert "check_persistence()" in script
    assert "install_persistence()" in script
    assert "repair_runtime()" in script
    assert "compose_persistence_result=%s" in script
    assert "compose_runtime_sha256=%s" in script
    assert "compose_flash_sha256=%s" in script
    assert "compose_hash_match=%s" in script
    assert "docker compose version" in script
    assert "awk -v begin=" in script


def test_unraid_upgrade_rollback_drill_requires_hard_checks_and_tag_round_trip():
    script = _script_text("unraid_image_upgrade_rollback_drill.sh")

    assert 'run_phase "phase_initial" "$FROM_TAG" "$FROM_SHA"' in script
    assert 'run_phase "phase_upgrade" "$TO_TAG" "$TO_SHA"' in script
    assert 'run_phase "phase_rollback" "$FROM_TAG" "$FROM_SHA"' in script
    assert 'summary_value "$latest_summary" skip_run_once' in script
    assert 'summary_value "$latest_summary" skip_restore' in script
    assert 'summary_value "$latest_summary" actual_runtime_source' in script
    assert 'summary_value "$latest_summary" actual_image_tag' in script
    assert "--mode image" in script
