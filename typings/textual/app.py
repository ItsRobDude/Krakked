from typing import Any, Iterable


class App:
    CSS_PATH: str
    BINDINGS: Iterable[Any]

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def run(self) -> Any: ...

    def query_one(self, selector: str, type: type[Any] | None = None) -> Any: ...


ComposeResult = Iterable[Any]
