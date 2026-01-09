import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from kraken_bot.ui.api import create_api
from kraken_bot.ui.context import AppContext
from kraken_bot.config_models import AppConfig, UIConfig, UIAuthConfig

@pytest.fixture
def mock_app_context():
    config = AppConfig(
        region=MagicMock(),
        universe=MagicMock(),
        market_data=MagicMock(),
        portfolio=MagicMock(),
        execution=MagicMock(),
        risk=MagicMock(),
        strategies=MagicMock(),
        ui=UIConfig(
            enabled=True,
            host="127.0.0.1",
            port=8000,
            base_path="",
            auth=UIAuthConfig(enabled=False, token=""),
        ),
        profiles={},
        session=MagicMock(),
        ml=MagicMock(),
    )

    ctx = MagicMock(spec=AppContext)
    ctx.config = config
    ctx.is_setup_mode = False
    return ctx

def test_security_headers_present(mock_app_context):
    """Verify that security headers are added to responses."""
    app = create_api(mock_app_context)
    client = TestClient(app)

    response = client.get("/api/health")
    assert response.status_code == 200

    headers = response.headers
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "same-origin"

def test_security_headers_on_error(mock_app_context):
    """Verify that security headers are present even on 404 responses."""
    app = create_api(mock_app_context)
    client = TestClient(app)

    response = client.get("/non-existent-path")
    assert response.status_code == 404

    headers = response.headers
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "same-origin"
