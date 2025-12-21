from kraken_bot import main
from kraken_bot.portfolio.exceptions import PortfolioSchemaError


class _DummyConfig:
    def __init__(self):
        class Portfolio:
            sync_interval_seconds = 300
            auto_migrate_schema = True
            db_path = "portfolio.db"

        class Strategies:
            loop_interval_seconds = 60

        class UI:
            enabled = False
            host = "localhost"
            port = 0

        class Execution:
            pass

        class MarketData:
            ws_timeframes = []
            backfill_timeframes = []
            metadata_path = None
            ws = {}

        self.portfolio = Portfolio()
        self.strategies = Strategies()
        self.ui = UI()
        self.execution = Execution()
        self.market_data = MarketData()


def test_run_exits_on_portfolio_schema_error(monkeypatch, caplog):
    def fake_bootstrap(*_, **__):
        return "client", _DummyConfig(), "rate"

    class FakeMarketData:
        def __init__(self, *_args, **_kwargs):
            pass

        def initialize(self):
            return None

        def shutdown(self):  # pragma: no cover - defensive
            return None

    def raise_schema_error(*_args, **_kwargs):
        raise PortfolioSchemaError(found=1, expected=2)

    monkeypatch.setattr(main, "bootstrap", fake_bootstrap)
    monkeypatch.setattr(main, "MarketDataAPI", FakeMarketData)
    monkeypatch.setattr(main, "PortfolioService", raise_schema_error)
    # Prevent main.run from nuking the caplog handler
    monkeypatch.setattr(main, "configure_logging", lambda **kwargs: None)

    # Prevent BotController from hanging in the main loop
    monkeypatch.setattr(
        main.BotController, "start_ui", lambda self: self.stop_event.set()
    )

    # In the new UI-first boot, run() calls bootstrap_locked_context() which does NOT
    # initialize PortfolioService, thus avoiding the schema error check at startup.
    # So exit_code should be 0 (success).
    exit_code = main.run()

    assert exit_code == 0
    # Schema check is deferred, so NO schema mismatch log is expected at boot.
