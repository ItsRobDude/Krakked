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
    assert 'if [ "$MODE" = "image" ] && [ "$ACTUAL_RUNTIME_SOURCE" != "image" ]' in script
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
    ):
        assert f"printf '{field}=%s\\n'" in script


def test_unraid_upgrade_rollback_drill_requires_hard_checks_and_tag_round_trip():
    script = _script_text("unraid_image_upgrade_rollback_drill.sh")

    assert "run_phase \"phase_initial\" \"$FROM_TAG\" \"$FROM_SHA\"" in script
    assert "run_phase \"phase_upgrade\" \"$TO_TAG\" \"$TO_SHA\"" in script
    assert "run_phase \"phase_rollback\" \"$FROM_TAG\" \"$FROM_SHA\"" in script
    assert 'summary_value "$latest_summary" skip_run_once' in script
    assert 'summary_value "$latest_summary" skip_restore' in script
    assert 'summary_value "$latest_summary" actual_runtime_source' in script
    assert 'summary_value "$latest_summary" actual_image_tag' in script
    assert "--mode image" in script
