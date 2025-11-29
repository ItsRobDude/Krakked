class Config:
    def __init__(self, app, host: str = "127.0.0.1", port: int = 8000, log_level: str | None = None, log_config=None):
        self.app = app
        self.host = host
        self.port = port
        self.log_level = log_level
        self.log_config = log_config


class Server:
    def __init__(self, config: Config):
        self.config = config
        self.should_exit = False

    def run(self):
        self.should_exit = True
        return True
