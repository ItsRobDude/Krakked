from typing import Any

class Response:
    text: str
    status_code: int
    def json(self) -> Any: ...
    def raise_for_status(self) -> None: ...

class RequestException(Exception): ...
class Timeout(RequestException): ...

class Session:
    headers: dict[str, str]
    def __init__(self) -> None: ...
    def request(
        self,
        method: str,
        url: str,
        params: Any | None = None,
        data: Any | None = None,
        headers: Any | None = None,
        cookies: Any | None = None,
        files: Any | None = None,
        auth: Any | None = None,
        timeout: float | tuple[float, float] | None = None,
        allow_redirects: bool = True,
        proxies: Any | None = None,
        hooks: Any | None = None,
        stream: bool | None = None,
        verify: bool | str | None = None,
        cert: str | tuple[str, str] | None = None,
        json: Any | None = None,
        **kwargs: Any,
    ) -> Response: ...
    def get(self, url: str, **kwargs: Any) -> Response: ...
    def post(self, url: str, **kwargs: Any) -> Response: ...

class HTTPError(RequestException):
    response: Response | None

def get(url: str, **kwargs: Any) -> Response: ...
def post(url: str, **kwargs: Any) -> Response: ...
