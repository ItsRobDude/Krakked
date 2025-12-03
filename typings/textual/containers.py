from typing import Any


class Container:
    display: bool

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...

    def __enter__(self) -> "Container":
        return self

    def __exit__(
        self, exc_type: type | None, exc: BaseException | None, tb: object | None
    ) -> None: ...


class Horizontal(Container):
    def __enter__(self) -> "Horizontal":
        return self

    def __exit__(
        self, exc_type: type | None, exc: BaseException | None, tb: object | None
    ) -> None: ...


class Vertical(Container):
    def __enter__(self) -> "Vertical":
        return self

    def __exit__(
        self, exc_type: type | None, exc: BaseException | None, tb: object | None
    ) -> None: ...
