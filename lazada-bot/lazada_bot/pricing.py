"""Price parsing and purchase-eligibility rules."""
from __future__ import annotations

import re

# Countries whose prices use "." for thousands and have no decimals in practice.
_DOT_THOUSANDS = {"vn", "id"}


def parse_price(text: str, country: str) -> float | None:
    """Parse a displayed price like 'RM1,299.00', 'Rp150.000' or '12.000 ₫'."""
    if not text:
        return None
    # Take the first number-ish run, so "RM45.00 RM60.00" yields 45.
    m = re.search(r"\d[\d.,]*", text)
    if not m:
        return None
    num = m.group(0).rstrip(".,")
    if country in _DOT_THOUSANDS:
        num = num.replace(".", "").replace(",", ".")
    else:
        num = num.replace(",", "")
    try:
        return float(num)
    except ValueError:
        return None


def purchase_decision(*, in_stock: bool, price: float | None, max_price: float,
                      quantity: int, spent: float, total_budget: float) -> tuple[bool, str]:
    """Return (should_buy, reason). Fails closed when price is unknown."""
    if not in_stock:
        return False, "out of stock"
    if price is None:
        return False, "price not found on page"
    if price > max_price:
        return False, f"price {price:g} above max {max_price:g}"
    cost = price * quantity
    if spent + cost > total_budget:
        return False, f"would exceed budget ({spent:g} spent + {cost:g} > {total_budget:g})"
    return True, f"in stock at {price:g}"
