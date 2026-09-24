"""Config loading and validation."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .store import normalize_product_url

# Lazada country sites. VN and ID use "." as the thousands separator.
DOMAINS = {
    "sg": "www.lazada.sg",
    "my": "www.lazada.com.my",
    "ph": "www.lazada.com.ph",
    "th": "www.lazada.co.th",
    "vn": "www.lazada.vn",
    "id": "www.lazada.co.id",
}

MODES = ("notify", "dry_run", "confirm", "auto")
MIN_POLL_SECONDS = 30

# Defaults are best guesses at Lazada's current markup and WILL drift.
# Every one can be overridden under `selectors:` in config.yaml.
DEFAULT_SELECTORS = {
    "price": ".pdp-price_type_normal",
    "buy_now_text": r"buy now|beli sekarang|mua ngay|ซื้อเลย|bilhin na",
    "quantity_increase": ".next-number-picker-handler-up",
    "checkout_total": ".checkout-order-total-fee",
    "place_order_text": r"place order|buat pesanan|đặt hàng|สั่งซื้อสินค้า",
    # Links on a product page that may point at the seller's store.
    "seller_link": ".seller-name__detail a, .seller-container a, a[href*='/shop/']",
}

# Sealed product types only. A name must match one of these...
DEFAULT_INCLUDE = (r"booster|elite trainer|\betb\b|collection|\btins?\b|blister|bundle"
                   r"|build\s*(?:&|and)\s*battle|\bdecks?\b")
# ...and none of these: accessories (checked first, so "Deck Box" or "Binder Collection"
# is rejected), singles/codes, and non-TCG merchandise.
DEFAULT_EXCLUDE = (r"sleeve|binder|portfolio|album|deck\s*(?:box|case)|card\s*(?:case|holder)"
                   r"|play\s*mat|top\s*loader|dice|damage counter|storage"
                   r"|\bsingle\b|code card|\bpromo\b"
                   r"|plush|figure|apparel|t-shirt|\btee\b|hoodie|mug|keychain|sticker|\bpins?\b")


class ConfigError(ValueError):
    pass


@dataclass
class Item:
    name: str
    url: str
    max_price: float
    quantity: int = 1


@dataclass
class Store:
    url: str
    slug: str | None = None           # auto-detected from `url` if not given
    discover: bool = True             # watch the store's listing for products
    max_price_each: float | None = None
    quantity: int = 1
    include: str = DEFAULT_INCLUDE    # regex on product name
    exclude: str = DEFAULT_EXCLUDE
    refresh_minutes: int = 15
    max_items: int = 30


@dataclass
class Config:
    country: str
    mode: str
    store: Store
    items: list[Item]
    total_budget: float
    shipping_allowance: float = 0.0
    poll_seconds: int = 60
    headless: bool = False
    profile_dir: str = ".lazada-profile"
    state_file: str = "state.json"
    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None
    selectors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_SELECTORS))

    @property
    def domain(self) -> str:
        return DOMAINS[self.country]


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise ConfigError(msg)


def parse_config(raw: dict) -> Config:
    _require(isinstance(raw, dict), "config must be a mapping")

    country = str(raw.get("country", "")).lower()
    _require(country in DOMAINS, f"country must be one of {sorted(DOMAINS)}")

    mode = raw.get("mode", "notify")
    _require(mode in MODES, f"mode must be one of {MODES}")

    # notify mode never spends, so the budget only matters for the buying modes.
    budget = raw.get("total_budget", float("inf") if mode == "notify" else None)
    _require(isinstance(budget, (int, float)) and budget > 0,
             "total_budget is required and must be > 0 unless mode is notify")

    shipping = raw.get("shipping_allowance", 0)
    _require(isinstance(shipping, (int, float)) and shipping >= 0,
             "shipping_allowance must be >= 0")

    poll = raw.get("poll_seconds", 60)
    _require(isinstance(poll, int) and poll >= MIN_POLL_SECONDS,
             f"poll_seconds must be an integer >= {MIN_POLL_SECONDS}")

    domain = DOMAINS[country]
    store = _parse_store(raw.get("store"), domain)

    raw_items = raw.get("items") or []
    _require(raw_items or store.discover,
             "add at least one item, or set store.discover: true")
    items = []
    for i, it in enumerate(raw_items):
        where = f"items[{i}]"
        _require(isinstance(it, dict), f"{where} must be a mapping")
        url = str(it.get("url", ""))
        _require(domain in url, f"{where}.url must be a {domain} product URL")
        max_price = it.get("max_price")
        _require(isinstance(max_price, (int, float)) and max_price > 0,
                 f"{where}.max_price is required and must be > 0")
        qty = it.get("quantity", 1)
        _require(isinstance(qty, int) and 1 <= qty <= 10,
                 f"{where}.quantity must be an integer from 1 to 10")
        url = normalize_product_url(url)
        items.append(Item(name=str(it.get("name") or url), url=url,
                          max_price=float(max_price), quantity=qty))

    selectors = dict(DEFAULT_SELECTORS)
    selectors.update(raw.get("selectors") or {})

    tg = raw.get("telegram") or {}
    return Config(
        country=country,
        mode=mode,
        store=store,
        items=items,
        total_budget=float(budget),
        shipping_allowance=float(shipping),
        poll_seconds=poll,
        headless=bool(raw.get("headless", False)),
        profile_dir=str(raw.get("profile_dir", ".lazada-profile")),
        state_file=str(raw.get("state_file", "state.json")),
        telegram_bot_token=tg.get("bot_token"),
        telegram_chat_id=str(tg["chat_id"]) if tg.get("chat_id") else None,
        selectors=selectors,
    )


def _parse_store(raw, domain: str) -> Store:
    _require(isinstance(raw, dict) and raw.get("url"),
             "store.url is required (the bot only buys from that store)")
    url = str(raw["url"])
    _require("lazada." in url, "store.url must be a Lazada link")
    discover = bool(raw.get("discover", True))
    max_price = raw.get("max_price_each")
    if discover:
        _require(isinstance(max_price, (int, float)) and max_price > 0,
                 "store.max_price_each is required and must be > 0 when discover is on")
    qty = raw.get("quantity", 1)
    _require(isinstance(qty, int) and 1 <= qty <= 10, "store.quantity must be 1 to 10")
    refresh = raw.get("refresh_minutes", 15)
    _require(isinstance(refresh, int) and refresh >= 5, "store.refresh_minutes must be >= 5")
    max_items = raw.get("max_items", 30)
    _require(isinstance(max_items, int) and 1 <= max_items <= 100,
             "store.max_items must be 1 to 100")
    for key in ("include", "exclude"):
        if key in raw:
            try:
                re.compile(str(raw[key] or ""))
            except re.error as e:
                raise ConfigError(f"store.{key} is not a valid regex: {e}") from e
    slug = raw.get("slug")
    return Store(
        url=url,
        slug=str(slug).lower() if slug else None,
        discover=discover,
        max_price_each=float(max_price) if max_price else None,
        quantity=qty,
        include=str(raw.get("include", DEFAULT_INCLUDE) or ""),
        exclude=str(raw.get("exclude", DEFAULT_EXCLUDE) or ""),
        refresh_minutes=refresh,
        max_items=max_items,
    )


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f))
