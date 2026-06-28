"""Unit tests for supplier_data.py parsing and mapping logic."""

import tempfile
from pathlib import Path

import pytest

from supplier_data import (
    SupplierCodeMapEntry,
    get_active_mapping_by_code,
    get_supplier_code_entries,
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
        1        10001       Vodka            1L   6    12.50     5053990107339
        """
        rows = parse_supplier_confirmation_rows(text)
        assert len(rows) == 1
        assert rows[0]["ean"] == "5053990107339"


class TestSupplierCodeMapping:
    """Tests for get_supplier_code_entries and related functions."""

    def test_get_supplier_code_entries_loads_valid_yaml(self) -> None:
        yaml_content = """items:
  - supplier_code: 10001
    mapping:
      plu: 1001
      servings_per_unit: 1
    comment: "Vodka supplier"
  - supplier_code: 10002
    mapping:
      plu: 1002
      servings_per_unit: 2
    ignore: false
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = Path(f.name)

        try:
            entries = get_supplier_code_entries(temp_path)
            assert len(entries) == 2
            assert entries[10001]["mapping"] is not None
            assert entries[10001]["mapping"]["plu"] == 1001
            assert entries[10001]["mapping"]["servings_per_unit"] == 1
            assert entries[10001]["comment"] == "Vodka supplier"
            assert entries[10002]["ignore"] is False
        finally:
            temp_path.unlink()

    def test_get_supplier_code_entries_rejects_invalid_servings_per_unit(self) -> None:
        yaml_content = """items:
  - supplier_code: 10001
    mapping:
      plu: 1001
      servings_per_unit: 0
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="servings_per_unit must be > 0"):
                get_supplier_code_entries(temp_path)
        finally:
            temp_path.unlink()

    def test_get_supplier_code_entries_requires_complete_mapping_fields(self) -> None:
        yaml_content = """items:
  - supplier_code: 10001
    mapping:
      plu: 1001
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = Path(f.name)

        try:
            with pytest.raises(ValueError, match="mapping must include plu and servings_per_unit"):
                get_supplier_code_entries(temp_path)
        finally:
            temp_path.unlink()

    def test_get_supplier_code_entries_handles_ignored_entries(self) -> None:
        yaml_content = """items:
  - supplier_code: 10001
    ignore: true
    comment: "Deprecated supplier"
  - supplier_code: 10002
    mapping:
      plu: 1002
      servings_per_unit: 1
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(yaml_content)
            temp_path = Path(f.name)

        try:
            entries = get_supplier_code_entries(temp_path)
            assert entries[10001]["ignore"] is True
            assert entries[10001]["mapping"] is None
            assert entries[10002]["ignore"] is False
        finally:
            temp_path.unlink()

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
