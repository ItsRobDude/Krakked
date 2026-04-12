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
