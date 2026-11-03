"""Production module: a tiny calculator (fixture prod code)."""


def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


def subtract(a: int, b: int) -> int:
    """Return the difference of two integers."""
    return a - b


def multiply(a: int, b: int) -> int:
    """Return the product of two integers."""
    return a * b


def divide(a: int, b: int) -> float:
    """Return the quotient, raising on division by zero."""
    if b == 0:
        raise ValueError("division by zero")
    return a / b


def _validate_operands(a, b):
    """Private helper — implementation detail (feeds the §5.11 detector)."""
    if not (isinstance(a, int) and isinstance(b, int)):
        raise TypeError("operands must be int")
    return True
