# Lazada Pokémon TCG restock bot

Watches specific Lazada product pages and, when an item is in stock at or under your price, goes to checkout for you. It uses your real logged-in Lazada account in a normal Chromium window, and it has hard spending limits.

## Before you use it

Automated purchasing is very likely against Lazada's Terms of Use. The realistic consequences are order cancellation or account suspension. The bot polls slowly (every 30 s at most), never solves CAPTCHAs or bypasses verification, and stops to ask you when Lazada challenges it. If you want less risk, run it in `confirm` mode: the bot does the watching and you click the final button yourself.

Only point it at listings from LazMall / official Pokémon stores. Fake TCG product is common on marketplaces, and a bot will happily buy a fake.

## Setup

```bash
cd lazada-bot
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp config.example.yaml config.yaml   # then edit it
```

Log in once. The session is saved to `.lazada-profile/`:

```bash
python -m lazada_bot login
```

While you're logged in, set a **default shipping address** and a **default payment method**. The bot can't pick either for you. Cash on delivery or a saved wallet gives the fewest extra steps. Cards may still prompt for an OTP.

## Usage

```bash
python -m lazada_bot check   # read-only: logs stock and price for each item. Run this first.
python -m lazada_bot run     # watch and act according to `mode`
```

| mode | what happens when an item qualifies |
|---|---|
| `dry_run` (default) | goes to checkout, logs the total, does **not** order |
| `confirm` | goes to checkout, alerts you, and leaves the tab open for you to place the order |
| `auto` | clicks Place Order, but only if the checkout total is ≤ `max_price × quantity + shipping_allowance` and within the remaining budget. Otherwise it falls back to `confirm`. |

Each item is handled once. `state.json` records what was bought and what was spent, so a restart never re-buys or overspends. Delete an entry from `state.json` to arm that item again.

## Limits you should know about

- **The selectors are unverified.** I couldn't reach Lazada from the environment this was built in. The defaults in `config.py` (`.pdp-price_type_normal`, "Buy Now" / "Place Order" text, and so on) are best guesses at Lazada's markup and will drift over time. Run `check` first. If the price comes back as "not found", inspect the page and override `selectors:` in `config.yaml`. Then do a `dry_run` on an in-stock item before you trust `auto`.
- **"Order submitted" may not mean paid.** Depending on the country and payment method, Lazada may show a payment page or an OTP after Place Order. The bot tells you to finish that step in the open tab. Always check My Orders.
- **Variants:** it buys whatever variant the product URL opens with. It doesn't pick sizes or bundles.
- **It isn't built to win hyped drops.** Polling every 30–60 s won't beat dedicated scalper bots on a hyped release. It's meant for catching restocks and price drops.

## Tests

```bash
LAZADA_BOT_CHROMIUM=/path/to/chromium pytest   # the env var is optional
```

`tests/test_flow.py` runs the whole flow (check, checkout, place order, fallbacks) in a real browser against mock Lazada pages. It doesn't test the real site.
