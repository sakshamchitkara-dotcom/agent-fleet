"""Price calculations for the shop."""


def apply_discount(price: float, percent: float) -> float:
    """Return `price` reduced by `percent` percent, rounded to cents."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be between 0 and 100")
    return round(price * percent / 100, 2)


def cart_total(items: list[tuple[float, int]], discount: float = 0) -> float:
    """Sum (unit_price, quantity) pairs and apply an optional discount."""
    subtotal = sum(price * qty for price, qty in items)
    return apply_discount(subtotal, discount)
