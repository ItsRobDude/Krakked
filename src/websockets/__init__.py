async def connect(*_args, **_kwargs):  # pragma: no cover - shim
    class _Dummy:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def recv(self):
            return ""

        async def send(self, *_args, **_kwargs):
            return None

    return _Dummy()
