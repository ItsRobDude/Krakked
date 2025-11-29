class BaseHTTPMiddleware:
    def __init__(self, app):
        self.app = app

    async def dispatch(self, request, call_next):  # pragma: no cover - shim
        return await call_next(request)
