def mean(xs):
    if not xs:
        raise ValueError("mean of empty data")
    return sum(xs) / len(xs)


def median(xs):
    if not xs:
        raise ValueError("median of empty data")
    s = sorted(xs)
    mid = len(s) // 2
    return s[mid]
