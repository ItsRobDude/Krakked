import os
import yaml
import pytest
from unittest import mock

from kraken_trader import config

@pytest.fixture
def mock_config_dir(tmp_path, monkeypatch):
    """Mocks the config directory variables to use a temporary path."""
    config_dir_path = tmp_path
    config_file_path = os.path.join(config_dir_path, "config.yaml")

    monkeypatch.setattr(config, 'CONFIG_DIR', str(config_dir_path))
    monkeypatch.setattr(config, 'CONFIG_PATH', str(config_file_path))

    yield str(config_dir_path)

def test_get_config_dir_creates_directory(mock_config_dir):
    """Tests that get_config_dir creates the directory if it doesn't exist."""
    # The directory is created by the load_config call in other tests,
    # so we just need to ensure it exists.
    config_dir = config.get_config_dir()
    assert os.path.exists(config_dir)
    assert config_dir == mock_config_dir

def test_load_config_creates_default_config(mock_config_dir):
    """Tests that load_config creates a default config.yaml if it doesn't exist."""
    cfg = config.load_config()

    assert cfg == config.DEFAULT_CONFIG

    config_path = os.path.join(mock_config_dir, "config.yaml")
    assert os.path.exists(config_path)

    with open(config_path, "r") as f:
        written_cfg = yaml.safe_load(f)
    assert written_cfg == config.DEFAULT_CONFIG

def test_load_config_loads_existing_config(mock_config_dir):
    """Tests that load_config loads an existing config.yaml."""
    custom_config = {
        "region": "EU",
        "supports_margin": True,
        "supports_futures": True,
        "default_quote": "EUR",
    }

    config_path = os.path.join(mock_config_dir, "config.yaml")
    with open(config_path, "w") as f:
        yaml.dump(custom_config, f)

    cfg = config.load_config()
    assert cfg == custom_config

def test_load_config_raises_error_on_corrupted_file(mock_config_dir):
    """Tests that load_config raises a YAMLError for a corrupted file."""
    config_path = os.path.join(mock_config_dir, "config.yaml")
    with open(config_path, "w") as f:
        f.write("this is not valid yaml: {")

    with pytest.raises(yaml.YAMLError):
        config.load_config()
