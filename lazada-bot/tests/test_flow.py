"""End-to-end flow against mock Lazada pages (no network)."""
import os

import pytest

from lazada_bot.bot import LazadaBot, StoreError, launch
from lazada_bot.config import parse_config
from lazada_bot.notify import Notifier
from lazada_bot.state import State

pw = pytest.importorskip("playwright.sync_api")

SHORT_LINK = "https://s.lazada.sg/s.Tr8yW?c=x"
STORE_URL = "https://www.lazada.sg/shop/pokemon-center/"
URL = "https://www.lazada.sg/products/etb-i1.html"
PLUSH = "https://www.lazada.sg/products/pikachu-plush-i2.html"

PRODUCT = """<html><body>
<h1>Pokemon TCG Elite Trainer Box</h1>
<span class="pdp-price pdp-price_type_normal">S${price}</span>
<div class="seller-name__detail"><a href="https://www.lazada.sg/shop/{seller}/?itemId=1">Store</a></div>
<span class="next-number-picker-handler-up">+</span>
<button {disabled} onclick="location.href='https://checkout.lazada.sg/shipping'">Buy Now</button>
</body></html>"""

CHECKOUT = """<html><body>
<div class="checkout-order-total-fee">S${total}</div>
<button onclick="location.href='https://checkout.lazada.sg/payment'">Place Order</button>
</body></html>"""

STORE = f"""<html><body>
<a href="{URL}?spm=a1"><img></a>
<a href="{URL}?spm=a2">Pokemon TCG Elite Trainer Box</a>
<a href="{PLUSH}">Pikachu Plush</a>
<a href="https://www.lazada.sg/products/other-shop-thing.html">no id</a>
</body></html>"""


def make(tmp_path, mode, *, price=79.9, total=83.9, disabled=False, seller="pokemon-center",
         discover=False, items=True):
    cfg = parse_config({
        "country": "sg", "mode": mode, "total_budget": 200, "headless": True,
        "shipping_allowance": 5, "profile_dir": str(tmp_path / "profile"),
        "state_file": str(tmp_path / "state.json"),
        "store": {"url": SHORT_LINK, "discover": discover, "max_price_each": 80},
        "items": [{"name": "ETB", "url": URL, "max_price": 80}] if items else [],
    })
    notes = []
    notifier = Notifier()
    notifier.send = notes.append
    bot = LazadaBot(cfg, State(cfg.state_file), notifier)
    pages = {
        "product": PRODUCT.format(price=price, seller=seller,
                                  disabled="disabled" if disabled else ""),
        "checkout": CHECKOUT.format(total=total),
        "store_redirect": STORE_URL,
    }
    return cfg, bot, notes, pages


def run_once(cfg, bot, pages):
    visited = []

    def handle(route):
        url = route.request.url
        visited.append(url)
        if url.startswith("https://s.lazada.sg/"):
            body = f"<script>location.replace({pages['store_redirect']!r})</script>"
            return route.fulfill(status=200, content_type="text/html", body=body)
        if "/products/" in url:
            body = pages["product"]
        elif url.endswith("/payment"):
            body = "<html><body>Payment</body></html>"
        elif "checkout.lazada" in url:
            body = pages["checkout"]
        else:
            body = STORE
        route.fulfill(status=200, content_type="text/html", body=body)

    with pw.sync_playwright() as p:
        os.environ.setdefault("LAZADA_BOT_CHROMIUM", "/opt/pw-browsers/chromium")
        ctx = launch(p, cfg)
        ctx.route("**/*", handle)
        try:
            bot.check_once(ctx)
        finally:
            ctx.close()
    return visited


def test_resolves_short_link_to_store(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "dry_run")
    run_once(cfg, bot, pages)
    assert bot.slug == "pokemon-center"


def test_unresolvable_store_stops(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "auto")
    pages["store_redirect"] = "https://www.lazada.sg/"
    with pytest.raises(StoreError):
        run_once(cfg, bot, pages)
    assert not bot.state.is_done(URL)


def test_auto_places_order(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "auto")
    visited = run_once(cfg, bot, pages)
    assert visited[-1].endswith("/payment")
    assert bot.state.data["items"][URL]["status"] == "order_submitted"
    assert bot.state.spent == pytest.approx(83.9)


def test_other_seller_never_bought(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "auto", seller="cardking-reseller")
    visited = run_once(cfg, bot, pages)
    assert not any("checkout" in u for u in visited)
    assert not bot.state.is_done(URL)


def test_discovers_store_products(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "dry_run", discover=True, items=False)
    run_once(cfg, bot, pages)
    assert list(bot.discovered) == [URL]  # plush filtered out, duplicate links merged
    assert bot.discovered[URL].name == "Pokemon TCG Elite Trainer Box"
    assert bot.state.data["items"][URL]["status"] == "dry_run"


def test_dry_run_never_places(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "dry_run")
    visited = run_once(cfg, bot, pages)
    assert not any(u.endswith("/payment") for u in visited)
    assert bot.state.data["items"][URL]["status"] == "dry_run"
    assert bot.state.spent == 0


def test_auto_downgrades_when_total_too_high(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "auto", total=120)
    visited = run_once(cfg, bot, pages)
    assert not any(u.endswith("/payment") for u in visited)
    assert bot.state.data["items"][URL]["status"] == "awaiting_confirmation"
    assert any("downgraded" in n for n in notes)


def test_out_of_stock_skips(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "auto", disabled=True)
    visited = run_once(cfg, bot, pages)
    assert not any("checkout" in u for u in visited)
    assert not bot.state.is_done(URL)


def test_over_max_price_skips(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "auto", price=95)
    run_once(cfg, bot, pages)
    assert not bot.state.is_done(URL)
