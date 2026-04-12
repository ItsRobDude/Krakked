from pathlib import Path

import appdirs  # type: ignore[import-untyped]

from krakked.config import load_config


def test_numeric_strings_are_accepted_for_int_fields(
    monkeypatch, tmp_path: Path
) -> None:
    """Quoted numeric YAML values should still be accepted for int config fields."""

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
execution:
  max_plan_age_seconds: "120"
ui:
  port: "8765"
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    app_config = load_config()

    assert app_config.execution.max_plan_age_seconds == 120
    assert app_config.ui.port == 8765


def test_bool_values_are_rejected_for_int_fields(monkeypatch, tmp_path: Path) -> None:
    """Booleans should not be accepted for integer config values."""

    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config_path = config_dir / "config.yaml"
    config_path.write_text(
        """
execution:
  max_plan_age_seconds: true
""".strip()
    )

    monkeypatch.setattr(appdirs, "user_config_dir", lambda appname: config_dir)
    monkeypatch.setattr(appdirs, "user_data_dir", lambda appname: data_dir)

    app_config = load_config()

    # Default comes from ExecutionConfig.max_plan_age_seconds
    assert app_config.execution.max_plan_age_seconds == 60
