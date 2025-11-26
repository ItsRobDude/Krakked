import os
import yaml
from appdirs import user_config_dir

APP_NAME = "kraken_bot"
CONFIG_DIR = user_config_dir(APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.yaml")

DEFAULT_CONFIG = {
    "region": "US_CA",
    "supports_margin": False,
    "supports_futures": False,
    "default_quote": "USD",
}

def get_config_dir() -> str:
    """Returns the application's configuration directory, creating it if necessary."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    return CONFIG_DIR

def load_config() -> dict:
    """
    Loads the configuration from config.yaml.

    If the file doesn't exist, it creates a default one.
    """
    get_config_dir()  # Ensure the directory exists
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(DEFAULT_CONFIG, f)
        return DEFAULT_CONFIG

    with open(CONFIG_PATH, "r") as f:
        try:
            config = yaml.safe_load(f)
            return config
        except yaml.YAMLError as e:
            # Handle potential corruption or invalid format
            print(f"Error loading {CONFIG_PATH}: {e}")
            raise
