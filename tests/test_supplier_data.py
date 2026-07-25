"""Unit tests for supplier_data.py parsing and mapping logic."""

from datetime import date

import pytest

from supplier_data import (
    SupplierCodeMapEntry,
    get_active_mapping_by_code,
    parse_supplier_confirmation_delivery_date,
    parse_supplier_confirmation_rows,
)


class TestSupplierDataParsing:
    """Tests for parse_supplier_confirmation_rows."""

    def test_parse_supplier_confirmation_rows_extracts_valid_rows(self) -> None:
        text = """
        Quantity Code        Description      Size Pack Price   EAN code
        1        10001       Vodka            1L   6    12.50   1234567890123
        2        10002       Gin              1L   6    15.00   2345678901234
        """
        rows = parse_supplier_confirmation_rows(text)
        assert len(rows) == 2
        assert rows[0]["supplier_code"] == 10001
        assert rows[0]["description"] == "Vodka"
        assert rows[0]["price_pence"] == 1250
        assert rows[0]["pack"] == 6
        assert rows[0]["ean"] == "1234567890123"
        assert rows[1]["supplier_code"] == 10002
        assert rows[1]["price_pence"] == 1500

    def test_parse_supplier_confirmation_rows_skips_invalid_rows(self) -> None:
        text = """
        Quantity Code        Description      Size Pack Price   EAN code
        1        10001       Vodka            1L   6    12.50   1234567890123
        2        BADCODE     Gin              1L   6    15.00   2345678901234
        3        10003       Rum              1L   xyz  18.00   3456789012345
        """
        rows = parse_supplier_confirmation_rows(text)
        assert len(rows) == 1
        assert rows[0]["supplier_code"] == 10001

    def test_parse_supplier_confirmation_rows_skips_invalid_price(self) -> None:
        text = """
        Quantity Code        Description      Size Pack Price   EAN code
        1        10001       Vodka            1L   6    BAD     1234567890123
        """
        rows = parse_supplier_confirmation_rows(text)
        assert rows == []

    def test_parse_supplier_confirmation_rows_handles_space_separated_rows(self) -> None:
        text = """
        Quantity Code Description Size Pack Price EAN code
        1 50001 TEST ALE KEG 11G 3.8%11G 1 99.99 6000000000001
        1 50002 TEST CIDER CASE 15 X 5500ML 15 45.00 6000000000002
        0 WEBREF Website Reference 1 1
        """
        rows = parse_supplier_confirmation_rows(text)
        assert len(rows) == 2
        assert rows[0]["supplier_code"] == 50001
        assert rows[0]["price_pence"] == 9999
        assert rows[0]["description"] == "TEST ALE KEG 11G"
        assert rows[0]["size"] == "3.8%11G"
        assert rows[1]["supplier_code"] == 50002
        assert rows[1]["size"] == "15 X 5500ML"

    def test_parse_supplier_confirmation_rows_handles_missing_header(self) -> None:
        text = """
        1        10001       Vodka            1L   6    12.50
        2        10002       Gin              1L   6    15.00
        """
        with pytest.raises(ValueError, match="must contain a header row"):
            parse_supplier_confirmation_rows(text)

    def test_parse_supplier_confirmation_rows_handles_empty_ean(self) -> None:
        text = """
        Quantity Code        Description      Size Pack Price   EAN code
        1        10001       Vodka            1L   6    12.50
        """
        rows = parse_supplier_confirmation_rows(text)
        assert len(rows) == 1
        assert rows[0]["ean"] == ""

    def test_parse_supplier_confirmation_rows_handles_whitespace(self) -> None:
        text = """
        Quantity Code        Description      Size Pack Price   EAN code
        1        10001       Vodka            1L   6    12.50     6000000000001
        """
        rows = parse_supplier_confirmation_rows(text)
        assert len(rows) == 1
        assert rows[0]["ean"] == "6000000000001"


class TestSupplierDeliveryDateParsing:
    def test_parse_supplier_confirmation_delivery_date(self) -> None:
        text = "Order:708806  Your ref:  Delivery date:17/07/26  A/c:D23014"

        assert parse_supplier_confirmation_delivery_date(text) == date(2026, 7, 17)

    def test_parse_supplier_confirmation_delivery_date_accepts_four_digit_year(self) -> None:
        text = "Delivery date: 17/07/2026"

        assert parse_supplier_confirmation_delivery_date(text) == date(2026, 7, 17)

    def test_parse_supplier_confirmation_delivery_date_requires_date(self) -> None:
        with pytest.raises(ValueError, match="must contain a delivery date"):
            parse_supplier_confirmation_delivery_date("Order:708806")

    def test_parse_supplier_confirmation_delivery_date_rejects_invalid_date(self) -> None:
        with pytest.raises(ValueError, match="invalid delivery date"):
            parse_supplier_confirmation_delivery_date("Delivery date:31/02/26")


class TestSupplierCodeMapping:
    """Tests for mapping helper functions."""

    def test_get_active_mapping_by_code_filters_ignored(self) -> None:
        entries: dict[int, SupplierCodeMapEntry] = {
            10001: {"mapping": {"plu": 1001, "servings_per_unit": 1}, "ignore": False, "comment": ""},
            10002: {"mapping": {"plu": 1002, "servings_per_unit": 1}, "ignore": True, "comment": ""},
            10003: {"mapping": None, "ignore": False, "comment": ""},
        }
        active = get_active_mapping_by_code(entries)
        assert len(active) == 1
        assert 10001 in active
        assert 10002 not in active
        assert 10003 not in active
