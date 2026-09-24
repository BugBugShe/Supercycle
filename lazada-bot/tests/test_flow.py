"""End-to-end flow against mock Lazada pages (no network)."""
import os

import pytest

from lazada_bot.bot import LazadaBot, launch
from lazada_bot.config import parse_config
from lazada_bot.notify import Notifier
from lazada_bot.state import State

pw = pytest.importorskip("playwright.sync_api")

PRODUCT = """<html><body>
<h1>Pokemon TCG Elite Trainer Box</h1>
<span class="pdp-price pdp-price_type_normal">S${price}</span>
<span class="next-number-picker-handler-up">+</span>
<button {disabled} onclick="location.href='https://checkout.lazada.sg/shipping'">Buy Now</button>
</body></html>"""

CHECKOUT = """<html><body>
<div class="checkout-order-total-fee">S${total}</div>
<button onclick="location.href='https://checkout.lazada.sg/payment'">Place Order</button>
</body></html>"""

URL = "https://www.lazada.sg/products/etb-i1.html"


def make(tmp_path, mode, *, price=79.9, total=83.9, disabled=False, shipping=5):
    cfg = parse_config({
        "country": "sg", "mode": mode, "total_budget": 200, "headless": True,
        "shipping_allowance": shipping, "profile_dir": str(tmp_path / "profile"),
        "state_file": str(tmp_path / "state.json"),
        "items": [{"name": "ETB", "url": URL, "max_price": 80}],
    })
    notes = []
    notifier = Notifier()
    notifier.send = notes.append
    bot = LazadaBot(cfg, State(cfg.state_file), notifier)
    pages = {
        "product": PRODUCT.format(price=price, disabled="disabled" if disabled else ""),
        "checkout": CHECKOUT.format(total=total),
    }
    return cfg, bot, notes, pages


def run_once(cfg, bot, pages):
    visited = []

    def handle(route):
        url = route.request.url
        visited.append(url)
        body = pages["checkout"] if "checkout.lazada" in url else pages["product"]
        if url.endswith("/payment"):
            body = "<html><body>Payment</body></html>"
        route.fulfill(status=200, content_type="text/html", body=body)

    with pw.sync_playwright() as p:
        os.environ.setdefault("LAZADA_BOT_CHROMIUM", "/opt/pw-browsers/chromium")
        ctx = launch(p, cfg)
        ctx.route("**/*", handle)
        bot.check_once(ctx)
        ctx.close()
    return visited


def test_auto_places_order(tmp_path):
    cfg, bot, notes, pages = make(tmp_path, "auto")
    visited = run_once(cfg, bot, pages)
    assert visited[-1].endswith("/payment")
    assert bot.state.data["items"][URL]["status"] == "order_submitted"
    assert bot.state.spent == pytest.approx(83.9)


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
