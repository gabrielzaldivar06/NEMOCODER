def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    total = sum(values)
    # BUG: wrong denominator
    return total / (len(values) - 1)
