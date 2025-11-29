class DataFrame:
    def __init__(self, *_args, **_kwargs):
        self.data = _args

    def to_parquet(self, *_args, **_kwargs):  # pragma: no cover - shim
        return None


def concat(_dfs, *_args, **_kwargs):  # pragma: no cover - shim
    return DataFrame(*_dfs)


def read_parquet(*_args, **_kwargs):  # pragma: no cover - shim
    return DataFrame()
