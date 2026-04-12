"""Tests for UI-first boot sequence."""

import signal

from krakked.config_loader import get_initial_ui_config
from krakked.main import BotController


def test_ui_first_boot_locked(monkeypatch, tmp_path):
    """
    Verifies that the BotController starts in locked mode without bootstrapping
    credentials or services.
    """
    # 1. Patch configuration directories to use tmp_path
    monkeypatch.setattr("krakked.config_loader.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("krakked.ui.routes.system.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("krakked.secrets.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("krakked.main.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("krakked.config.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("krakked.ui.routes.config.get_config_dir", lambda: tmp_path)

    # 2. Patch bootstrap to ensure it is NOT called
    def mock_bootstrap(*args, **kwargs):
        raise RuntimeError("bootstrap() must NOT be called during locked boot!")

    monkeypatch.setattr("krakked.main.bootstrap", mock_bootstrap)

    # 3. Patch start_ui to immediately stop execution (simulating loop exit)
    # This prevents uvicorn from actually starting and blocking the test
    def mock_start_ui(self):
        self.stop_event.set()

    monkeypatch.setattr(BotController, "start_ui", mock_start_ui)

    # 4. Patch signal.signal to avoid interfering with test runner
    monkeypatch.setattr(signal, "signal", lambda *args: None)

    # 5. Run controller
    controller = BotController(allow_interactive_setup=False)
    exit_code = controller.run()

    # 6. Assertions
    assert exit_code == 0
    assert controller.is_setup_mode is True
    assert controller.context is not None
    assert controller.context.is_setup_mode is True
    assert controller.context.client is None
    assert controller.context.market_data is None


def test_default_ui_bind_config_uses_container_env(monkeypatch):
    monkeypatch.setenv("KRAKKED_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("KRAKKED_UI_PORT", "8080")

    assert get_initial_ui_config() == {
        "enabled": True,
        "host": "0.0.0.0",
        "port": 8080,
    }


def test_bootstrap_locked_context_writes_container_safe_ui_defaults(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("krakked.config_loader.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("krakked.main.get_config_dir", lambda: tmp_path)
    monkeypatch.setenv("KRAKKED_UI_HOST", "0.0.0.0")
    monkeypatch.setenv("KRAKKED_UI_PORT", "8080")

    controller = BotController(allow_interactive_setup=False)
    ctx = controller.bootstrap_locked_context()

    assert ctx.config.ui.host == "0.0.0.0"
    assert ctx.config.ui.port == 8080
