"""Store identity and product-link helpers (pure functions, no browser)."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

# Top-level Lazada paths that are never a store slug.
_RESERVED = {"products", "catalog", "shop", "wow", "customer", "cart", "checkout", "tag", "i"}
_PRODUCT_ID = re.compile(r"-i(\d+)(?:-s\d+)?\.html", re.I)


def extract_slug(url: str) -> str | None:
    """'https://www.lazada.sg/shop/pokemon-center/?x' or '.../pokemon-center/' -> 'pokemon-center'."""
    parts = [p for p in urlsplit(url).path.split("/") if p]
    if parts and parts[0].lower() == "shop":
        parts = parts[1:]
    if not parts or parts[0].lower() in _RESERVED or "." in parts[0]:
        return None
    return parts[0].lower()


def seller_matches(hrefs: list[str], slug: str) -> bool:
    """True if any link on the product page points at the store with this slug."""
    return any(extract_slug(h) == slug for h in hrefs if "lazada." in h)


def product_id(url: str) -> str | None:
    m = _PRODUCT_ID.search(urlsplit(url).path)
    return m.group(1) if m else None


def normalize_product_url(url: str) -> str:
    """Drop query/fragment (tracking params) so one product has one state entry."""
    s = urlsplit(url)
    return f"https://{s.netloc}{s.path}"


def name_matches(name: str, include: str, exclude: str) -> bool:
    if exclude and re.search(exclude, name, re.I):
        return False
    return not include or bool(re.search(include, name, re.I))
