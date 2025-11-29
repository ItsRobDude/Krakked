class BaseModel:
    model_config = {}

    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)


class ConfigDict(dict):
    pass
