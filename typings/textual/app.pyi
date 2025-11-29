from typing import Any, Iterable

class App:
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def run(self) -> Any: ...

ComposeResult = Iterable[Any]
