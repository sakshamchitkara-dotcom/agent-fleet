def add_item(name, qty, stock={}):
    """Return a stock mapping with `qty` more of `name` (a new mapping unless one is passed)."""
    stock[name] = stock.get(name, 0) + qty
    return stock


def total(stock):
    return sum(stock.values())
