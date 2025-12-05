from __future__ import annotations

from pathlib import Path

import pytest

from kraken_bot import config_loader as cl
from kraken_bot.config_models import (
    AppConfig,
    MarketDataConfig,
    PortfolioConfig,
    RegionCapabilities,
    RegionProfile,
    StrategiesConfig,
    StrategyConfig,
    UniverseConfig,
)


def _minimal_config() -> AppConfig:
    return AppConfig(
        region=RegionProfile("US_CA", RegionCapabilities(False, False, False)),
        universe=UniverseConfig(include_pairs=[], exclude_pairs=[], min_24h_volume_usd=0),
        market_data=MarketDataConfig(
            ws={}, ohlc_store={}, backfill_timeframes=[], ws_timeframes=[]
        ),
        portfolio=PortfolioConfig(),
        strategies=StrategiesConfig(
            enabled=[],
            configs={"demo": StrategyConfig(name="demo", type="demo", enabled=True)},
        ),
    )


def test_dump_runtime_overrides_atomic_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path
    path = config_dir / cl.RUNTIME_OVERRIDES_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("risk: {max_risk_per_trade_pct: 1.0}\n", encoding="utf-8")

    original = path.read_text(encoding="utf-8")

    def failing_safe_dump(data, f):
        f.write("partial: true\n")
        raise RuntimeError("boom")

    monkeypatch.setattr(cl.yaml, "safe_dump", failing_safe_dump)

    with pytest.raises(RuntimeError):
        cl.dump_runtime_overrides(_minimal_config(), config_dir=config_dir)

    assert path.read_text(encoding="utf-8") == original
    assert not any(config_dir.glob(f"{path.name}*.tmp"))
