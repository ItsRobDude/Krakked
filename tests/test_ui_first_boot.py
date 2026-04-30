"""Tests for UI-first boot sequence."""

import signal
from types import SimpleNamespace

from krakked.config_loader import get_initial_ui_config
from krakked.main import BotController
from krakked.ui.context import AppContext


def test_ui_first_boot_locked(monkeypatch, tmp_path):
    """
    Verifies that the BotController starts in locked mode without bootstrapping
    credentials or services.
    """
    # 1. Patch configuration directories to use tmp_path
    monkeypatch.delenv("KRAKEN_API_KEY", raising=False)
    monkeypatch.delenv("KRAKEN_API_SECRET", raising=False)
    monkeypatch.delenv("KRAKKED_SECRET_PW", raising=False)
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


def test_bootstrap_locked_context_seeds_starter_profile(monkeypatch, tmp_path):
    monkeypatch.setattr("krakked.config_loader.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("krakked.main.get_config_dir", lambda: tmp_path)

    controller = BotController(allow_interactive_setup=False)
    ctx = controller.bootstrap_locked_context()

    assert "Default" in ctx.config.profiles
    assert ctx.config.session.profile_name == "Default"
    assert ctx.session.profile_name == "Default"
    assert (tmp_path / "profiles" / "Default.yaml").exists()


def test_run_auto_initializes_when_env_credentials_exist(monkeypatch):
    monkeypatch.setenv("KRAKEN_API_KEY", "key")
    monkeypatch.setenv("KRAKEN_API_SECRET", "secret")
    monkeypatch.setattr(signal, "signal", lambda *args: None)

    locked_context = AppContext(
        config=SimpleNamespace(ui=SimpleNamespace(enabled=False)),
        client=None,
        market_data=None,
        portfolio_service=None,
        portfolio=None,
        strategy_engine=None,
        execution_service=None,
        metrics=None,
        is_setup_mode=True,
    )
    ready_context = AppContext(
        config=SimpleNamespace(ui=SimpleNamespace(enabled=False)),
        client=object(),
        market_data=None,
        portfolio_service=None,
        portfolio=None,
        strategy_engine=None,
        execution_service=None,
        metrics=None,
        is_setup_mode=False,
    )

    monkeypatch.setattr(
        BotController, "bootstrap_locked_context", lambda self: locked_context
    )
    monkeypatch.setattr(BotController, "bootstrap_context", lambda self: ready_context)
    monkeypatch.setattr(BotController, "start_ui", lambda self: self.stop_event.set())

    controller = BotController(allow_interactive_setup=False)
    exit_code = controller.run()

    assert exit_code == 0
    assert controller.context is ready_context
    assert controller.is_setup_mode is False


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
