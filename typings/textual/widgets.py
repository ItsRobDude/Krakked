from typing import Any


class Widget:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        ...


class Static(Widget):
    def render(self) -> str:
        ...


class DataTable(Widget):
    cursor_type: str

    def clear(self, *, columns: bool | None = None, rows: bool | None = None) -> None:
        ...

    def add_columns(self, *labels: str) -> None:
        ...

    def add_row(self, *cells: Any) -> None:
        ...


class Footer(Widget):
    ...
