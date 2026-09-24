"""Browser automation: watch product pages and check out when rules allow."""
from __future__ import annotations

import logging
import os
import random
import re
import time

from playwright.sync_api import BrowserContext, Page, TimeoutError as PWTimeout, sync_playwright

from .config import Config, Item
from .notify import Notifier
from .pricing import parse_price, purchase_decision
from .state import State

log = logging.getLogger("lazada_bot")

# Lazada's anti-bot interstitial. We never try to solve it: we pause and ask a human.
_BLOCK_URL = re.compile(r"punish|captcha", re.I)
_BLOCK_SELECTOR = "#nc_1_n1z, .nc-container, iframe[src*='captcha']"
_LOGIN_URL = re.compile(r"member\.lazada|/login", re.I)
_CHECKOUT_URL = re.compile(r"checkout\.lazada", re.I)
HUMAN_WAIT_SECONDS = 300


class BlockedError(RuntimeError):
    pass


def launch(p, cfg: Config, headless: bool | None = None) -> BrowserContext:
    """Persistent profile keeps the Lazada login between runs."""
    return p.chromium.launch_persistent_context(
        cfg.profile_dir,
        headless=cfg.headless if headless is None else headless,
        executable_path=os.environ.get("LAZADA_BOT_CHROMIUM") or None,
        viewport={"width": 1280, "height": 900},
    )


def _button(page: Page, pattern: str):
    rx = re.compile(pattern, re.I)
    return page.get_by_role("button", name=rx).or_(page.get_by_text(rx)).first


def _is_blocked(page: Page) -> bool:
    return bool(_BLOCK_URL.search(page.url)) or page.locator(_BLOCK_SELECTOR).count() > 0


class LazadaBot:
    def __init__(self, cfg: Config, state: State, notifier: Notifier):
        self.cfg = cfg
        self.state = state
        self.notify = notifier
        self.sel = cfg.selectors

    # --- page helpers -------------------------------------------------------

    def _wait_for_human(self, page: Page, what: str) -> None:
        if self.cfg.headless:
            raise BlockedError(f"{what} while headless; rerun with headless: false")
        self.notify.send(f"Action needed in the browser window: {what}. "
                         f"Waiting up to {HUMAN_WAIT_SECONDS // 60} min.")
        deadline = time.time() + HUMAN_WAIT_SECONDS
        while time.time() < deadline:
            page.wait_for_timeout(3000)
            if not _is_blocked(page) and not _LOGIN_URL.search(page.url):
                return
        raise BlockedError(f"{what} was not resolved in time")

    def _goto(self, page: Page, url: str) -> None:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        if _is_blocked(page):
            self._wait_for_human(page, "Lazada CAPTCHA / verification")
        if _LOGIN_URL.search(page.url):
            self._wait_for_human(page, "Lazada login")

    # --- core ---------------------------------------------------------------

    def inspect(self, page: Page, item: Item) -> tuple[bool, float | None]:
        """Return (in_stock, unit_price) for a product page."""
        self._goto(page, item.url)
        price = None
        try:
            text = page.locator(self.sel["price"]).first.inner_text(timeout=15000)
            price = parse_price(text, self.cfg.country)
        except PWTimeout:
            log.info("[%s] price element not found (selector: %s)", item.name, self.sel["price"])
        buy = _button(page, self.sel["buy_now_text"])
        try:
            buy.wait_for(state="visible", timeout=10000)
            in_stock = buy.is_enabled()
        except PWTimeout:
            in_stock = False
        return in_stock, price

    def checkout(self, page: Page, item: Item, unit_price: float) -> None:
        for _ in range(item.quantity - 1):
            page.locator(self.sel["quantity_increase"]).first.click()
        _button(page, self.sel["buy_now_text"]).click()

        try:
            page.wait_for_url(_CHECKOUT_URL, timeout=30000)
        except PWTimeout:
            if _LOGIN_URL.search(page.url) or _is_blocked(page):
                self._wait_for_human(page, "login/verification before checkout")
                page.wait_for_url(_CHECKOUT_URL, timeout=30000)
            else:
                raise

        total = None
        try:
            text = page.locator(self.sel["checkout_total"]).first.inner_text(timeout=20000)
            total = parse_price(text, self.cfg.country)
        except PWTimeout:
            pass

        remaining = self.cfg.total_budget - self.state.spent
        cap = min(unit_price * item.quantity + self.cfg.shipping_allowance, remaining)
        mode = self.cfg.mode
        if mode == "auto" and (total is None or total > cap):
            # Fail closed: an unreadable or surprising total never gets auto-placed.
            reason = "could not read order total" if total is None else f"total {total:g} > cap {cap:g}"
            self.notify.send(f"[{item.name}] {reason}; downgraded to manual confirmation.")
            mode = "confirm"

        shown = "unknown" if total is None else f"{total:g}"
        if mode == "dry_run":
            self.notify.send(f"[DRY RUN] {item.name}: reached checkout, total {shown}. "
                             "Order NOT placed.")
            self.state.record(item.url, "dry_run", 0.0)
            page.close()
            return

        # Reserve budget up front (worst case) so later items can't overspend.
        reserved = total if total is not None else unit_price * item.quantity + self.cfg.shipping_allowance
        if mode == "confirm":
            self.state.record(item.url, "awaiting_confirmation", reserved)
            self.notify.send(f"{item.name} is in stock at checkout, total {shown}. "
                             "Review and place the order in the open browser tab.")
            return  # leave the tab open for the human

        _button(page, self.sel["place_order_text"]).click()
        self.state.record(item.url, "order_submitted", reserved)
        page.wait_for_load_state("domcontentloaded")
        self.notify.send(f"Placed order for {item.name}, total {shown}. If Lazada shows a "
                         "payment/OTP step, finish it in the open tab. Verify in My Orders.")

    def check_once(self, ctx: BrowserContext, buy: bool = True) -> None:
        for item in self.cfg.items:
            if self.state.is_done(item.url):
                continue
            page = ctx.new_page()
            keep_open = False
            try:
                in_stock, price = self.inspect(page, item)
                ok, reason = purchase_decision(
                    in_stock=in_stock, price=price, max_price=item.max_price,
                    quantity=item.quantity, spent=self.state.spent,
                    total_budget=self.cfg.total_budget)
                log.info("[%s] %s -> %s", item.name, reason, "BUY" if ok and buy else "skip")
                if ok and buy:
                    self.checkout(page, item, price)
                    keep_open = not page.is_closed()
            except BlockedError as e:
                self.notify.send(f"[{item.name}] blocked: {e}")
            except Exception as e:
                log.exception("[%s] error: %s", item.name, e)
            finally:
                if not keep_open and not page.is_closed():
                    page.close()
            time.sleep(random.uniform(2, 5))  # space out requests between items

    def run(self) -> None:
        with sync_playwright() as p:
            ctx = launch(p, self.cfg)
            self.notify.send(f"Bot started in {self.cfg.mode} mode watching "
                             f"{len(self.cfg.items)} item(s).")
            while any(not self.state.is_done(i.url) for i in self.cfg.items):
                self.check_once(ctx)
                delay = self.cfg.poll_seconds * random.uniform(1.0, 1.2)
                log.info("sleeping %.0fs", delay)
                time.sleep(delay)
            self.notify.send("All items handled. Leaving the browser open; Ctrl+C to exit.")
            while True:
                time.sleep(3600)
