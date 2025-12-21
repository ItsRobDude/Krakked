"""Tests for UI-first boot sequence."""

import signal

from kraken_bot.main import BotController


def test_ui_first_boot_locked(monkeypatch, tmp_path):
    """
    Verifies that the BotController starts in locked mode without bootstrapping
    credentials or services.
    """
    # 1. Patch configuration directories to use tmp_path
    monkeypatch.setattr("kraken_bot.config_loader.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.ui.routes.system.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.secrets.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.main.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.config.get_config_dir", lambda: tmp_path)
    monkeypatch.setattr("kraken_bot.ui.routes.config.get_config_dir", lambda: tmp_path)

    # 2. Patch bootstrap to ensure it is NOT called
    def mock_bootstrap(*args, **kwargs):
        raise RuntimeError("bootstrap() must NOT be called during locked boot!")

    monkeypatch.setattr("kraken_bot.main.bootstrap", mock_bootstrap)

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
