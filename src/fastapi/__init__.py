class Request:
    def __init__(self):
        self.headers = {}
        self.url = type("_URL", (), {"path": ""})()
        self.state = type("_State", (), {})()


class FastAPI:
    def __init__(self, middleware=None):
        self.middleware_setup = middleware or []
        self.state = type("_State", (), {})()

    def include_router(self, *_args, **_kwargs):  # pragma: no cover - shim
        return None

    def middleware(self, *_args, **_kwargs):  # pragma: no cover - shim
        def decorator(func):
            return func

        return decorator


def Depends(*_args, **_kwargs):  # pragma: no cover - shim
    return None


class APIRouter:
    def __init__(self, *args, **kwargs):
        self.routes = []

    def get(self, *args, **kwargs):  # pragma: no cover - shim
        def decorator(func):
            return func

        return decorator

    post = get
    put = get
    patch = get
    delete = get

    def add_api_route(self, *args, **kwargs):  # pragma: no cover - shim
        return None


class HTTPException(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


from .middleware import Middleware  # noqa: E402
from .responses import JSONResponse  # noqa: E402
