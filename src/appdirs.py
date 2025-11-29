from pathlib import Path


def user_config_dir(appname: str):
    return str(Path.home() / f".{appname}")


def user_data_dir(appname: str):
    return str(Path.home() / f".{appname}")
