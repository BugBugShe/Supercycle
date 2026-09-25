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
from .store import extract_slug, name_matches, normalize_product_url, product_id, seller_matches

log = logging.getLogger("lazada_bot")

# Lazada's anti-bot interstitial. We never try to solve it: we pause and ask a human.
_BLOCK_URL = re.compile(r"punish|captcha", re.I)
_BLOCK_SELECTOR = "#nc_1_n1z, .nc-container, iframe[src*='captcha']"
_LOGIN_URL = re.compile(r"member\.lazada|/login", re.I)
_CHECKOUT_URL = re.compile(r"checkout\.lazada", re.I)
HUMAN_WAIT_SECONDS = 300


class BlockedError(RuntimeError):
    pass


class StoreError(RuntimeError):
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
        self.slug: str | None = cfg.store.slug
        self.discovered: dict[str, Item] = {}
        self.last_discovery = 0.0
        self.available: set[str] = set()  # notify mode: items already alerted as in stock

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

    # --- store --------------------------------------------------------------

    def resolve_store(self, ctx: BrowserContext) -> str:
        """Follow the store link (e.g. an s.lazada short link) to learn the store's slug."""
        if self.slug:
            return self.slug
        page = ctx.new_page()
        try:
            self._goto(page, self.cfg.store.url)
            try:  # short links may redirect via HTTP or via script
                page.wait_for_url(lambda u: self.cfg.domain in u, timeout=20000)
            except PWTimeout:
                pass
            slug = extract_slug(page.url)
            if not slug or self.cfg.domain not in page.url:
                raise StoreError(f"could not identify the store from {page.url!r}; "
                                 "set store.slug in config.yaml")
            log.info("store resolved: %s -> slug %r", page.url, slug)
            self.slug = slug
            return slug
        finally:
            page.close()

    def _collect_products(self, page: Page, url: str) -> dict[str, str]:
        """Return {normalized product url: name} for product links on a listing page."""
        self._goto(page, url)
        for _ in range(6):  # listings lazy-load as you scroll
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1200)
        links = page.eval_on_selector_all(
            "a[href*='/products/']",
            "els => els.map(e => [e.href, (e.title || e.getAttribute('aria-label') || e.innerText || '').trim()])")
        found: dict[str, str] = {}
        for href, name in links:
            if self.cfg.domain not in href or not product_id(href):
                continue
            key = normalize_product_url(href)
            if len(name) > len(found.get(key, "")):  # image links have no text
                found[key] = name.split("\n")[0]
            else:
                found.setdefault(key, "")
        return found

    def discover(self, ctx: BrowserContext) -> None:
        store = self.cfg.store
        page = ctx.new_page()
        try:
            base = f"https://{self.cfg.domain}/{self.slug}/"
            found = self._collect_products(
                page, base + "?q=All-Products&from=wangpu&langFlag=en&pageTypeId=2")
            if not found:
                found = self._collect_products(page, base)
        finally:
            page.close()

        first_run = not self.last_discovery
        self.last_discovery = time.time()
        known = {i.url for i in self.cfg.items}
        new = []
        for url, name in found.items():
            if url in self.discovered or url in known:
                continue
            if not name_matches(name, store.include, store.exclude):
                log.info("store: ignoring %r (name filter)", name or url)
                continue
            if len(self.discovered) >= store.max_items:
                log.warning("store: max_items (%d) reached; ignoring %s", store.max_items, url)
                break
            item = Item(name=name or url, url=url, max_price=store.max_price_each,
                        quantity=store.quantity)
            self.discovered[url] = item
            new.append(item)
        log.info("store: %d product links, watching %d discovered item(s)",
                 len(found), len(self.discovered))
        if first_run:
            if not found:
                self.notify.send("Found no products on the store page. The listing markup may "
                                 "have changed; add items manually under `items:`.")
        else:
            for item in new:
                self.notify.send(f"New Pokemon Center listing: {item.name}\n{item.url}")

    def watchlist(self) -> list[Item]:
        return list(self.cfg.items) + list(self.discovered.values())

    # --- core ---------------------------------------------------------------

    def seller_ok(self, page: Page) -> bool:
        try:
            page.locator(self.sel["seller_link"]).first.wait_for(state="attached", timeout=10000)
        except PWTimeout:
            return False
        hrefs = page.eval_on_selector_all(self.sel["seller_link"], "els => els.map(e => e.href)")
        return seller_matches(hrefs, self.slug)

    def inspect(self, page: Page, item: Item) -> tuple[bool, bool, float | None]:
        """Return (seller_ok, in_stock, unit_price) for a product page."""
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
        return self.seller_ok(page), in_stock, price

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

    def _alert_available(self, item: Item, price: float) -> None:
        """Alert once per restock: again only after the item has gone out of stock."""
        if item.url in self.available:
            return
        self.available.add(item.url)
        self.notify.send(f"IN STOCK at Pokemon Center: {item.name} for {price:g}\n{item.url}")

    def check_once(self, ctx: BrowserContext, buy: bool = True) -> None:
        """One pass over the watchlist. buy=False (the `check` command) only logs."""
        notify_only = self.cfg.mode == "notify"
        self.resolve_store(ctx)
        if self.cfg.store.discover and (
                time.time() - self.last_discovery >= self.cfg.store.refresh_minutes * 60):
            try:
                self.discover(ctx)
            except BlockedError as e:
                self.notify.send(f"store discovery blocked: {e}")
        for item in self.watchlist():
            if self.state.is_done(item.url):
                continue
            page = ctx.new_page()
            keep_open = False
            try:
                seller, in_stock, price = self.inspect(page, item)
                ok, reason = purchase_decision(
                    seller_ok=seller, in_stock=in_stock, price=price, max_price=item.max_price,
                    quantity=item.quantity, spent=self.state.spent,
                    total_budget=float("inf") if notify_only else self.cfg.total_budget)
                action = "skip" if not (ok and buy) else "ALERT" if notify_only else "BUY"
                log.info("[%s] %s -> %s", item.name, reason, action)
                if not in_stock or (price is not None and price > item.max_price):
                    self.available.discard(item.url)  # re-arm for the next restock
                if ok and buy and notify_only:
                    self._alert_available(item, price)
                elif ok and buy:
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
            self.resolve_store(ctx)
            self.notify.send(f"Bot started in {self.cfg.mode} mode, store {self.slug!r}.")
            # With discovery on, new listings can appear at any time, so keep going.
            while self.cfg.store.discover or any(
                    not self.state.is_done(i.url) for i in self.watchlist()):
                self.check_once(ctx)
                delay = self.cfg.poll_seconds * random.uniform(1.0, 1.2)
                log.info("sleeping %.0fs", delay)
                time.sleep(delay)
            self.notify.send("All items handled. Leaving the browser open; Ctrl+C to exit.")
            while True:
                time.sleep(3600)
