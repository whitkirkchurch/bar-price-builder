from __future__ import annotations

from typing import TYPE_CHECKING

import price_check

if TYPE_CHECKING:
    from loyverse import TillProduct
    from price_list import PluPrice


def _product(
    plu: int,
    name: str,
    price: int,
    till_price_set: bool = True,
    available_for_sale: bool = True,
) -> TillProduct:
    return {
        "plu": plu,
        "name": name,
        "price": price,
        "till_price_set": till_price_set,
        "available_for_sale": available_for_sale,
        "item_id": "item-1",
        "variant_id": "variant-1",
        "cost_pence": None,
        "barcode": None,
    }


def test_validate_till_prices_reports_missing_and_mismatch(monkeypatch, capsys) -> None:
    monkeypatch.setattr(price_check, "get_all_ignored_plus", list)

    till_products: dict[int, TillProduct] = {
        1001: _product(1001, "Vodka", 1250, till_price_set=False),
        1002: _product(1002, "Gin", 1500),
        1003: _product(1003, "Rum", 1800),
    }

    plus_in_price_list: dict[int, PluPrice] = {
        1003: {"price": 1700},
    }

    price_check.validate_till_prices(till_products, plus_in_price_list)

    output = capsys.readouterr().out
    assert "NO TILL PRICE: PLU 1001 (Vodka)" in output
    assert "MISSING: PLU 1002 (Gin)" in output
    assert "PRICE MISMATCH: PLU 1003 (Rum)" in output


def test_validate_till_prices_skips_unavailable_and_ignored(monkeypatch, capsys) -> None:
    monkeypatch.setattr(price_check, "get_all_ignored_plus", lambda: [2002])

    till_products: dict[int, TillProduct] = {
        2001: _product(2001, "Not For Sale", 1000, available_for_sale=False),
        2002: _product(2002, "Ignored", 1000),
    }

    price_check.validate_till_prices(till_products, plus_in_price_list={})

    output = capsys.readouterr().out
    assert output == ""


def test_warn_for_ignored_plus_not_on_till_and_not_for_sale(monkeypatch, capsys) -> None:
    monkeypatch.setattr(price_check, "get_all_ignored_plus", lambda: [3001, 3002])

    till_products: dict[int, TillProduct] = {
        3002: _product(3002, "Hidden SKU", 900, available_for_sale=False),
    }

    price_check.warn_for_ignored_plus_not_on_till(till_products)

    output = capsys.readouterr().out
    assert "NOT REAL PLU: PLU 3001" in output
    assert "IGNORED + NOT FOR SALE: PLU 3002 (Hidden SKU)" in output


def test_warn_for_configured_plus_not_on_till(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        price_check,
        "get_all_plus_in_price_list",
        lambda: {4001: {"price": 1000}, 4002: {"price": 1200}},
    )

    till_products: dict[int, TillProduct] = {
        4002: _product(4002, "Present SKU", 1200),
    }

    price_check.warn_for_configured_plus_not_on_till(till_products)

    output = capsys.readouterr().out
    assert "NOT REAL PLU: PLU 4001 is in prices.yaml" in output
    assert "PLU 4002" not in output
