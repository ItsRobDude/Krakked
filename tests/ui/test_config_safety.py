
import pytest
from unittest.mock import MagicMock
from fastapi.testclient import TestClient
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext
from kraken_bot.config import AppConfig, ExecutionConfig
from kraken_bot.market_data.api import MarketDataAPI
from kraken_bot.connection.exceptions import ServiceUnavailableError

@pytest.fixture
def mock_context():
    ctx = MagicMock(spec=AppContext)
    ctx.config = MagicMock(spec=AppConfig)
    ctx.config.execution = MagicMock(spec=ExecutionConfig)
    ctx.config.execution.mode = "paper"
    ctx.config.execution.allow_live_trading = False
    ctx.config.ui = MagicMock()
    ctx.config.ui.base_path = "/"
    ctx.config.ui.read_only = False
    ctx.config.ui.auth = MagicMock(enabled=False)
    ctx.session = MagicMock()
    ctx.session.active = False
    ctx.session.profile_name = None
    ctx.is_setup_mode = False
    ctx.market_data = MagicMock(spec=MarketDataAPI)
    ctx.market_data.get_health_status.return_value = None
    ctx.client = MagicMock()

    # Defaults
    ctx.config.profiles = {}

    return ctx

@pytest.fixture
def client(mock_context):
    app = create_api(mock_context)
    return TestClient(app)

def test_config_apply_rejects_execution_mode(client, mock_context):
    payload = {
        "config": {
            "execution": {
                "mode": "live"
            }
        },
        "dry_run": True
    }
    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "Execution 'mode' cannot be modified" in data["error"]

def test_config_apply_rejects_allow_live_trading(client, mock_context):
    payload = {
        "config": {
            "execution": {
                "allow_live_trading": True
            }
        },
        "dry_run": True
    }
    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "Execution 'allow_live_trading' cannot be modified" in data["error"]

def test_create_profile_rejects_base_config_execution_mode(client, mock_context):
    payload = {
        "name": "dangerous_profile",
        "default_mode": "paper",
        "base_config": {
            "execution": {
                "allow_live_trading": True
            }
        }
    }
    response = client.post("/api/system/profiles", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "Execution 'allow_live_trading' cannot be set" in data["error"]

def test_config_apply_blocks_on_universe_validation_failure(client, mock_context):
    mock_context.market_data.validate_pairs.side_effect = ServiceUnavailableError("Kraken down")

    payload = {
        "config": {
            "universe": {
                "include_pairs": ["XBTUSD"]
            }
        },
        "dry_run": True
    }
    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is not None
    assert "Universe validation unavailable" in data["error"]

def test_config_apply_succeeds_on_valid_universe(client, mock_context):
    mock_context.market_data.validate_pairs.return_value = [] # No invalid pairs

    payload = {
        "config": {
            "universe": {
                "include_pairs": ["XBTUSD"]
            }
        },
        "dry_run": True
    }
    response = client.post("/api/config/apply", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert data["error"] is None
    assert data["data"]["status"] == "valid"
