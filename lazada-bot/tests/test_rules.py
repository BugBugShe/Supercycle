import pytest

from lazada_bot.config import ConfigError, parse_config
from lazada_bot.pricing import parse_price, purchase_decision
from lazada_bot.state import State

BASE = {
    "country": "sg",
    "total_budget": 200,
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
    kw = dict(max_price=80, quantity=1, spent=0, total_budget=200)
    assert purchase_decision(in_stock=True, price=79, **kw)[0]
    assert not purchase_decision(in_stock=False, price=79, **kw)[0]
    assert not purchase_decision(in_stock=True, price=None, **kw)[0]
    assert not purchase_decision(in_stock=True, price=81, **kw)[0]
    assert not purchase_decision(in_stock=True, price=79, max_price=80, quantity=3,
                                 spent=0, total_budget=200)[0]
    assert not purchase_decision(in_stock=True, price=79, max_price=80, quantity=1,
                                 spent=150, total_budget=200)[0]


def test_config_defaults_to_dry_run():
    assert parse_config(BASE).mode == "dry_run"


@pytest.mark.parametrize("patch", [
    {"country": "us"},
    {"mode": "yolo"},
    {"total_budget": 0},
    {"poll_seconds": 5},
    {"items": []},
    {"items": [{"url": "https://www.lazada.com.my/products/x.html", "max_price": 10}]},
    {"items": [{"url": "https://www.lazada.sg/products/x.html"}]},
    {"items": [{"url": "https://www.lazada.sg/products/x.html", "max_price": 10, "quantity": 50}]},
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
