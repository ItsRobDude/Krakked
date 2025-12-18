
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient

from kraken_bot.ui.api import create_api
from kraken_bot.config import AppConfig, ExecutionConfig, RegionProfile, RegionCapabilities, UniverseConfig, MarketDataConfig, PortfolioConfig, SessionConfig
from kraken_bot.execution.models import LocalOrder

@pytest.fixture
def mock_context():
    ctx = MagicMock()
    # Populate required fields for AppConfig
    ctx.config = AppConfig(
        region=RegionProfile(
            code="US",
            default_quote="USD",
            capabilities=RegionCapabilities(
                supports_margin=True,
                supports_futures=False,
                supports_staking=True
            )
        ),
        universe=UniverseConfig(
            include_pairs=["XBT/USD"],
            exclude_pairs=[],
            min_24h_volume_usd=0.0
        ),
        market_data=MarketDataConfig(
            ws={},
            ohlc_store={},
            backfill_timeframes=[],
            ws_timeframes=[]
        ),
        portfolio=PortfolioConfig(db_path="test.db")
    )
    # Ensure execution config is set correctly
    ctx.config.execution = ExecutionConfig(mode="paper", allow_live_trading=False)

    # Use real SessionConfig to avoid YAML serialization issues with MagicMocks
    ctx.session = SessionConfig(
        active=False,
        profile_name=None,
        mode="paper",
        loop_interval_sec=60,
        ml_enabled=True,
        emergency_flatten=False
    )

    # Ensure is_setup_mode is False so middleware doesn't block requests
    ctx.is_setup_mode = False

    # Mock services
    ctx.execution_service = MagicMock()
    ctx.portfolio = MagicMock()
    ctx.strategy_engine = MagicMock()

    return ctx

@pytest.fixture
def client(mock_context):
    app = create_api(mock_context)
    return TestClient(app)

def test_flatten_all_fails_if_cancel_fails(client, mock_context):
    """Test that flatten execution is blocked if cancel_all raises exception."""
    mock_context.execution_service.cancel_all.side_effect = Exception("Cancel Failed")
    mock_context.execution_service.get_open_orders.return_value = [] # Even if empty list returned later

    # Mock dump_runtime_overrides to prevent file I/O
    with patch("kraken_bot.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = client.post("/api/execution/flatten_all")
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is not None
        assert "Flatten armed but waiting" in data["error"]
        assert "cancel_all failed" in data["error"]

        # Verify execute_plan was NOT called
        mock_context.execution_service.execute_plan.assert_not_called()
        # Verify emergency flag was set
        assert mock_context.session.emergency_flatten is True
        mock_dump.assert_called_once()

def test_flatten_all_fails_if_open_orders_remain(client, mock_context):
    """Test that flatten execution is blocked if open orders remain."""
    mock_context.execution_service.cancel_all.return_value = None # Success
    # Mock open orders remaining
    mock_context.execution_service.get_open_orders.return_value = [
        LocalOrder(local_id="1", plan_id="p", strategy_id="s", pair="P", side="buy", order_type="m")
    ]

    with patch("kraken_bot.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = client.post("/api/execution/flatten_all")
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is not None
        assert "waiting for open orders" in data["error"]

        # Verify execute_plan was NOT called
        mock_context.execution_service.execute_plan.assert_not_called()
        assert mock_context.session.emergency_flatten is True
        mock_dump.assert_called_once()

def test_flatten_all_executes_if_clean(client, mock_context):
    """Test that flatten execution proceeds if state is clean."""
    mock_context.execution_service.cancel_all.return_value = None
    mock_context.execution_service.get_open_orders.return_value = []
    # Mock sync success
    mock_context.portfolio.last_sync_ok = True

    # Mock plan
    plan = MagicMock()
    plan.plan_id = "flatten_plan"
    plan.actions = [1]
    mock_context.strategy_engine.build_emergency_flatten_plan.return_value = plan

    # Mock result
    result = MagicMock()
    result.plan_id = "flatten_plan"
    result.orders = []
    result.errors = []
    result.warnings = []
    # Add dummy started_at for serialization if needed, but the model handles it?
    # Actually ExecutionResult creates started_at in init if not provided?
    # Wait, _serialize_execution_result accesses result.started_at
    from datetime import datetime
    result.started_at = datetime.now()
    result.completed_at = datetime.now()
    result.success = True

    mock_context.execution_service.execute_plan.return_value = result

    with patch("kraken_bot.ui.routes.execution.dump_runtime_overrides") as mock_dump:
        response = client.post("/api/execution/flatten_all")
        assert response.status_code == 200
        data = response.json()

        assert data["error"] is None
        assert data["data"]["plan_id"] == "flatten_plan"

        mock_context.execution_service.execute_plan.assert_called_once()
        assert mock_context.session.emergency_flatten is True # Still sets flag for persistence
        mock_dump.assert_called_once()
