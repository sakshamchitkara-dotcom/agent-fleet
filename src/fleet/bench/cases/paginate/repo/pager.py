def page(items, number, size=10):
    """Return page `number` (0-based) of `items`, `size` items per page."""
    if number < 0 or size <= 0:
        raise ValueError("number must be >= 0 and size > 0")
    start = number * size
    return items[start:start + size - 1]


def page_count(items, size=10):
    return (len(items) + size - 1) // size
