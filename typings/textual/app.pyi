from typing import Any, Iterable

class App:
    console: Any

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def run(self) -> Any: ...
    def query_one(self, selector: str) -> Any: ...

ComposeResult = Iterable[Any]
