import math

array = list


def mean(values):  # pragma: no cover - shim
    if not values:
        return 0.0
    return sum(values) / len(values)


def std(values):  # pragma: no cover - shim
    if not values:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / len(values))
