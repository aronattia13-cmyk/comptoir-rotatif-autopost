import json
from decimal import Decimal
from pathlib import Path

import pytest

from comptoir import catalog

FIX = Path(__file__).parent / "fixtures" / "products_snapshot.json"


@pytest.fixture
def products():
    return catalog.normalize_products_json(json.loads(FIX.read_text()))


def test_prices_are_verbatim(products):
    raw = {p["handle"]: p["variants"][0]["price"] for p in json.loads(FIX.read_text())["products"]}
    for p in products:
        assert str(p.price) == raw[p.handle]


@pytest.mark.parametrize("title,lot", [
    ("Lot de 50 Turbines raccord rapide KAVO - LED triple spray.", 50),
    ("Lot de 5 Contre angle 1:5 ( bague rouge ) LED SPRAY", 5),
    ("lot de 10 détartreurs", 10),
    ("Raccord rapide type MULTIFLEX KAVO", 1),
])
def test_lot_size_from_title(title, lot):
    assert catalog.parse_lot_size(title) == lot


def test_lot_from_title_not_handle(products):
    p = next(p for p in products if p.handle.startswith("10-turbines"))
    assert p.lot_size == 50
    assert any("handle indique 10" in w for w in p.warnings)


def test_lots_5_and_50_are_distinct(products):
    turbines = sorted((p for p in products if p.category == "turbine"), key=lambda p: p.lot_size)
    assert [t.lot_size for t in turbines] == [5, 50]
    assert turbines[0].price != turbines[1].price


def test_unavailable_never_publishable():
    payload = json.loads(FIX.read_text())
    payload["products"][0]["variants"][0]["available"] = False
    prods = catalog.normalize_products_json(payload)
    assert prods[0] not in catalog.publishable(prods)


def test_missing_price_never_publishable():
    payload = json.loads(FIX.read_text())
    payload["products"][1]["variants"][0]["price"] = ""
    prods = catalog.normalize_products_json(payload)
    assert prods[1] not in catalog.publishable(prods)


def test_duplicate_is_excluded(products):
    dups = [p for p in products if any(w.startswith("doublon") for w in p.warnings)]
    assert len(dups) == 1
    assert dups[0] not in catalog.publishable(products)


def test_categories(products):
    cats = {p.category for p in products}
    assert cats == {"turbine", "contre-angle", "detartreur", "autre"}


@pytest.mark.parametrize("value,expected", [
    ("4709.99", "4 709,99 €"),
    ("99.99", "99,99 €"),
    ("6779.99", "6 779,99 €"),
    ("1234567.5", "1 234 567,50 €"),
])
def test_format_price(value, expected):
    assert catalog.format_price(Decimal(value)) == expected


@pytest.mark.parametrize("title,expected", [
    ("Lot de 50 DETARTREURS COMPTATIBLES EMS WOODPECKER AVEC LED",
     "Détartreurs compatibles EMS Woodpecker avec LED"),
    ("Lot de 5 Contre angle 1:5 ( bague rouge ) LED SPRAY", "Contre-angle 1:5 (bague rouge) LED spray"),
    ("Lot de 5 Turbine raccord rapide KAVO - LED triple spray.", "Turbine raccord rapide KaVo – LED triple spray"),
    ("Lot de 5 Contre-angle bague bleue 1:1  40 000 tours / mn LED simple spray",
     "Contre-angle bague bleue 1:1 40 000 tr/min LED simple spray"),
])
def test_clean_name(title, expected):
    assert catalog.clean_name(title) == expected


def test_invalid_price_rejected():
    for bad in ("", None, "0", "-3", "abc"):
        with pytest.raises(catalog.CatalogError):
            catalog.parse_price(bad)
