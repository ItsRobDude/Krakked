from typing import Generic, TypeVar

T = TypeVar("T")

class Reactive(Generic[T]):
    def __get__(self, obj: object, objtype: type | None = None) -> T: ...
    def __set__(self, obj: object, value: T) -> None: ...


def reactive(value: T) -> Reactive[T]: ...
