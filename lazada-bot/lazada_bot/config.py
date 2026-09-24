"""Config loading and validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Lazada country sites. VN and ID use "." as the thousands separator.
DOMAINS = {
    "sg": "www.lazada.sg",
    "my": "www.lazada.com.my",
    "ph": "www.lazada.com.ph",
    "th": "www.lazada.co.th",
    "vn": "www.lazada.vn",
    "id": "www.lazada.co.id",
}

MODES = ("dry_run", "confirm", "auto")
MIN_POLL_SECONDS = 30

# Defaults are best guesses at Lazada's current markup and WILL drift.
# Every one can be overridden under `selectors:` in config.yaml.
DEFAULT_SELECTORS = {
    "price": ".pdp-price_type_normal",
    "buy_now_text": r"buy now|beli sekarang|mua ngay|ซื้อเลย|bilhin na",
    "quantity_increase": ".next-number-picker-handler-up",
    "checkout_total": ".checkout-order-total-fee",
    "place_order_text": r"place order|buat pesanan|đặt hàng|สั่งซื้อสินค้า",
}


class ConfigError(ValueError):
    pass


@dataclass
class Item:
    name: str
    url: str
    max_price: float
    quantity: int = 1


@dataclass
class Config:
    country: str
    mode: str
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

    mode = raw.get("mode", "dry_run")
    _require(mode in MODES, f"mode must be one of {MODES}")

    budget = raw.get("total_budget")
    _require(isinstance(budget, (int, float)) and budget > 0,
             "total_budget is required and must be > 0")

    shipping = raw.get("shipping_allowance", 0)
    _require(isinstance(shipping, (int, float)) and shipping >= 0,
             "shipping_allowance must be >= 0")

    poll = raw.get("poll_seconds", 60)
    _require(isinstance(poll, int) and poll >= MIN_POLL_SECONDS,
             f"poll_seconds must be an integer >= {MIN_POLL_SECONDS}")

    raw_items = raw.get("items") or []
    _require(raw_items, "at least one item is required")
    domain = DOMAINS[country]
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
        items.append(Item(name=str(it.get("name") or url), url=url,
                          max_price=float(max_price), quantity=qty))

    selectors = dict(DEFAULT_SELECTORS)
    selectors.update(raw.get("selectors") or {})

    tg = raw.get("telegram") or {}
    return Config(
        country=country,
        mode=mode,
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


def load_config(path: str | Path) -> Config:
    with open(path, encoding="utf-8") as f:
        return parse_config(yaml.safe_load(f))
