from pathlib import Path

from starlette.testclient import TestClient

from krakked.ui.api import create_api


def test_create_api_uses_ui_dist_dir_env_override(
    monkeypatch, tmp_path: Path, mock_context
):
    ui_dist = tmp_path / "ui-dist"
    ui_dist.mkdir()
    (ui_dist / "index.html").write_text("<html><body>Krakked UI</body></html>")

    monkeypatch.setenv("UI_DIST_DIR", str(ui_dist))

    app = create_api(mock_context)
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert "Krakked UI" in response.text


def test_create_api_serves_ui_under_base_path(
    monkeypatch, tmp_path: Path, mock_context
):
    ui_dist = tmp_path / "ui-dist"
    assets_dir = ui_dist / "assets"
    assets_dir.mkdir(parents=True)
    (ui_dist / "index.html").write_text(
        '<html><body>Krakked UI<script type="module" src="./assets/index.js"></script></body></html>'
    )
    (assets_dir / "index.js").write_text('console.log("krakked")')

    mock_context.config.ui.base_path = "/krakked"
    monkeypatch.setenv("UI_DIST_DIR", str(ui_dist))

    app = create_api(mock_context)
    client = TestClient(app)

    response = client.get("/krakked/")
    asset = client.get("/krakked/assets/index.js")

    assert response.status_code == 200
    assert "Krakked UI" in response.text
    assert asset.status_code == 200
