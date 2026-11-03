"""Production module: order pricing (fixture prod code)."""

DISCOUNT = 0.1           # public module-level constant
_MAX_ITEMS = 100         # internal cap — implementation detail (§5.11)


def price_order(items: list, discount: float = DISCOUNT) -> float:
    """Price an order: subtotal reduced by the discount."""
    subtotal = sum(items)
    return round(subtotal * (1 - discount), 2)


def apply_coupon(price: float, code: str) -> float:
    """Apply a coupon code to a price."""
    if code == "SAVE10":
        return round(price * 0.9, 2)
    return price
