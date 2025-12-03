from __future__ import annotations

from typing import Any, Iterable, Sequence

class Index:
    def duplicated(self, keep: str | bool = ...) -> Any: ...
    def __ge__(self, other: Any) -> Any: ...

class DataFrame:
    index: Index

    def __init__(
        self,
        data: Any = ...,
        index: Any | None = ...,
        columns: Any | None = ...,
        dtype: Any | None = ...,
        copy: bool = ...,
    ) -> None: ...
    def __len__(self) -> int: ...
    def set_index(
        self,
        keys: Any,
        drop: bool = ...,
        append: bool = ...,
        inplace: bool = ...,
        verify_integrity: bool = ...,
    ) -> DataFrame: ...
    def sort_index(
        self,
        axis: int | str = ...,
        level: Any = ...,
        ascending: bool | Sequence[bool] = ...,
        inplace: bool = ...,
        kind: str = ...,
        na_position: str = ...,
        sort_remaining: bool = ...,
        ignore_index: bool = ...,
        key: Any = ...,
    ) -> DataFrame: ...
    def reset_index(
        self,
        level: Any = ...,
        drop: bool = ...,
        inplace: bool = ...,
        col_level: int = ...,
        col_fill: str | None = ...,
        names: Any = ...,
        allow_duplicates: bool = ...,
    ) -> DataFrame: ...
    def to_dict(self, orient: str = ..., into: Any = ...) -> Any: ...
    def to_parquet(self, path: Any, *args: Any, **kwargs: Any) -> None: ...
    def tail(self, n: int = ...) -> DataFrame: ...
    def __getitem__(self, key: Any) -> DataFrame: ...

def read_parquet(path: Any, *args: Any, **kwargs: Any) -> DataFrame: ...
def concat(
    objs: Sequence[DataFrame] | Iterable[DataFrame],
    axis: int | str = ...,
    *args: Any,
    **kwargs: Any,
) -> DataFrame: ...
