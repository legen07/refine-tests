"""Production module: warehouse lookups (fixture prod code)."""


def lookup(item: str) -> dict:
    """Look up the current stock record for an item."""
    return {"item": item, "qty": 7}


def restock(item: str, qty: int) -> dict:
    """Restock an item, returning the updated record."""
    return {"item": item, "qty": qty, "restocked": True}
