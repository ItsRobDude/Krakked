from typing import Generic, TypeVar

T = TypeVar("T")


class reactive(Generic[T]):
    def __init__(self, default: T | None = None) -> None:
        self.default = default

    def __get__(self, obj: object, objtype: type | None = None) -> T:
        ...

    def __set__(self, obj: object, value: T) -> None:
        ...


Reactive = reactive
