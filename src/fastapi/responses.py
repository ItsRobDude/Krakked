class JSONResponse:
    def __init__(self, content=None, status_code: int = 200):
        self.content = content
        self.status_code = status_code
        self.headers = {}
