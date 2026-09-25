import pytest

from lazada_bot.config import ConfigError, parse_config
from lazada_bot.pricing import parse_price, purchase_decision
from lazada_bot.state import State
from lazada_bot.store import extract_slug, name_matches, normalize_product_url, product_id, seller_matches

BASE = {
    "country": "sg",
    "total_budget": 200,
    "store": {"url": "https://s.lazada.sg/s.Tr8yW?c=x", "max_price_each": 100},
    "items": [{"name": "ETB", "url": "https://www.lazada.sg/products/x-i1.html", "max_price": 80}],
}


@pytest.mark.parametrize("text,country,expected", [
    ("S$79.90", "sg", 79.90),
    ("RM1,299.00", "my", 1299.0),
    ("₱2,450", "ph", 2450.0),
    ("฿1,290", "th", 1290.0),
    ("Rp150.000", "id", 150000.0),
    ("1.250.000 ₫", "vn", 1250000.0),
    ("RM45.00 RM60.00", "my", 45.0),
    ("", "sg", None),
    ("Free", "sg", None),
])
def test_parse_price(text, country, expected):
    assert parse_price(text, country) == expected


def test_decision_fails_closed():
    kw = dict(seller_ok=True, max_price=80, quantity=1, spent=0, total_budget=200)
    assert purchase_decision(in_stock=True, price=79, **kw)[0]
    assert not purchase_decision(in_stock=False, price=79, **kw)[0]
    assert not purchase_decision(in_stock=True, price=None, **kw)[0]
    assert not purchase_decision(in_stock=True, price=81, **kw)[0]
    assert not purchase_decision(**{**kw, "seller_ok": False}, in_stock=True, price=79)[0]
    assert not purchase_decision(**{**kw, "quantity": 3}, in_stock=True, price=79)[0]
    assert not purchase_decision(**{**kw, "spent": 150}, in_stock=True, price=79)[0]


def test_config_defaults_to_notify():
    assert parse_config(BASE).mode == "notify"


def test_budget_optional_only_in_notify_mode():
    no_budget = {k: v for k, v in BASE.items() if k != "total_budget"}
    assert parse_config({**no_budget, "mode": "notify"}).total_budget == float("inf")
    with pytest.raises(ConfigError):
        parse_config({**no_budget, "mode": "auto"})


@pytest.mark.parametrize("patch", [
    {"country": "us"},
    {"mode": "yolo"},
    {"total_budget": 0},
    {"poll_seconds": 5},
    {"items": [{"url": "https://www.lazada.com.my/products/x.html", "max_price": 10}]},
    {"items": [{"url": "https://www.lazada.sg/products/x.html"}]},
    {"items": [{"url": "https://www.lazada.sg/products/x.html", "max_price": 10, "quantity": 50}]},
    {"store": None},
    {"store": {"url": "https://example.com/shop"}},
    {"store": {"url": "https://s.lazada.sg/x"}},  # discover on, no max_price_each
    {"store": {"url": "https://s.lazada.sg/x", "discover": False}, "items": []},
    {"store": {"url": "https://s.lazada.sg/x", "max_price_each": 50, "include": "("}},
])
def test_config_rejects(patch):
    with pytest.raises(ConfigError):
        parse_config({**BASE, **patch})


def test_state_persists(tmp_path):
    path = tmp_path / "state.json"
    s = State(path)
    s.record("u1", "order_submitted", 42.5)
    s2 = State(path)
    assert s2.is_done("u1") and s2.spent == 42.5


def test_items_optional_with_discovery():
    cfg = parse_config({**BASE, "items": []})
    assert cfg.store.discover and cfg.items == []


def test_item_urls_normalized():
    cfg = parse_config({**BASE, "items": [
        {"url": "https://www.lazada.sg/products/x-i1.html?spm=abc#r", "max_price": 5}]})
    assert cfg.items[0].url == "https://www.lazada.sg/products/x-i1.html"


@pytest.mark.parametrize("url,slug", [
    ("https://www.lazada.sg/shop/pokemon-center-singapore/?spm=a", "pokemon-center-singapore"),
    ("https://www.lazada.sg/pokemon-center-singapore/?q=All-Products", "pokemon-center-singapore"),
    ("//www.lazada.sg/shop/Pokemon-Center", "pokemon-center"),
    ("https://www.lazada.sg/products/etb-i123.html", None),
    ("https://www.lazada.sg/catalog/?q=pokemon", None),
    ("https://www.lazada.sg/", None),
])
def test_extract_slug(url, slug):
    assert extract_slug(url) == slug


def test_seller_matches():
    slug = "pokemon-center"
    assert seller_matches(["https://www.lazada.sg/shop/pokemon-center/?itemId=1"], slug)
    assert not seller_matches(["https://www.lazada.sg/shop/pokemon-center-resale/"], slug)
    assert not seller_matches(["https://www.lazada.sg/shop/cardking/"], slug)
    assert not seller_matches([], slug)


def test_product_helpers():
    u = "https://www.lazada.sg/products/pokemon-etb-i2733-s1234.html?spm=x"
    assert product_id(u) == "2733"
    assert normalize_product_url(u) == "https://www.lazada.sg/products/pokemon-etb-i2733-s1234.html"
    assert product_id("https://www.lazada.sg/shop/x/") is None


@pytest.mark.parametrize("name,keep", [
    ("Pokemon TCG: Scarlet & Violet Elite Trainer Box", True),
    ("Pokémon TCG: Prismatic Evolutions Booster Bundle", True),
    ("Pokemon TCG Booster Display Box (36 packs)", True),
    ("Pokémon TCG: Charizard ex Premium Collection", True),
    ("Pokemon TCG Paldea Partners Mini Tin", True),
    ("Pokemon TCG 3-Pack Blister", True),
    ("Pokemon TCG Build & Battle Box", True),
    ("Pokemon TCG League Battle Deck", True),
    ("Pokemon TCG Card Sleeves (65)", False),
    ("Pokemon TCG 9-Pocket Binder", False),
    ("Pokemon TCG Binder Collection", False),
    ("Pokemon TCG Deck Box", False),
    ("Pokemon TCG Elite Trainer Box Card Sleeves", False),
    ("Pokemon TCG Playmat", False),
    ("Pokemon TCG Portfolio", False),
    ("Pokemon TCG Code Card", False),
    ("Pikachu Plush Collection", False),
    ("Pokemon Trading Card", False),  # no sealed type named
])
def test_name_filter_sealed_only(name, keep):
    cfg = parse_config(BASE).store
    assert name_matches(name, cfg.include, cfg.exclude) is keep


def test_relative_paths_resolve_next_to_config(tmp_path):
    import yaml
    from lazada_bot.config import load_config
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(BASE))
    cfg = load_config(tmp_path / "config.yaml")
    assert cfg.state_file == str(tmp_path / "state.json")
    assert cfg.profile_dir == str(tmp_path / ".lazada-profile")
