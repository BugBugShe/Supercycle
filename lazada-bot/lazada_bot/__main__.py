"""CLI: python -m lazada_bot {login,check,run} [-c config.yaml]"""
from __future__ import annotations

import argparse
import logging
import sys

from playwright.sync_api import sync_playwright

from .bot import LazadaBot, StoreError, launch
from .config import ConfigError, load_config
from .notify import Notifier
from .state import State


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lazada_bot")
    ap.add_argument("command", choices=["login", "check", "run"],
                    help="login: sign in once; check: report stock/price, never buys; run: watch and buy")
    ap.add_argument("-c", "--config", default="config.yaml")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config(args.config)
    except (OSError, ConfigError) as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 2

    if args.command == "login":
        with sync_playwright() as p:
            ctx = launch(p, cfg, headless=False)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(f"https://{cfg.domain}/")
            input("Log in in the browser window, confirm your default address and payment "
                  "method, then press Enter here to save the session... ")
            ctx.close()
        return 0

    bot = LazadaBot(cfg, State(cfg.state_file), Notifier(cfg.telegram_bot_token, cfg.telegram_chat_id))
    if args.command == "check":
        with sync_playwright() as p:
            ctx = launch(p, cfg)
            try:
                bot.check_once(ctx, buy=False)
            except StoreError as e:
                print(f"Store error: {e}", file=sys.stderr)
                return 2
            finally:
                ctx.close()
        return 0

    try:
        bot.run()
    except StoreError as e:
        print(f"Store error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
