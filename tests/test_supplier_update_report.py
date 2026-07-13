"""Tests for supplier update report formatting and mapping behaviour."""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from supplier_data import get_supplier_code_entries
from supplier_updates import (
    SupplierUpdateReport,
    SupplierUpdateSummary,
    format_supplier_update_report,
    run_supplier_cost_updates,
)

CONFIRMATION_TEXT = """
Quantity Code        Description      Size Pack Price   EAN code
1        10001       Vodka            1L   6    12.50   1234567890123
2        19999       Mystery Spirit   1L   6    20.00   9999999999999
"""


MAPPING_YAML = """---
items:
  - supplier_code: 10001
    mapping:
      plu: 1001
      servings_per_unit: 1
    comment: Vodka
"""


def _write_mapping_file(path: Path) -> None:
    path.write_text(MAPPING_YAML)


def test_format_supplier_update_report_includes_unmapped_codes() -> None:
    report = SupplierUpdateReport(
        summary=SupplierUpdateSummary(
            parsed_rows=2,
            mapped_rows=1,
            missing_supplier_codes=1,
            ignored_supplier_rows=0,
            missing_plus_on_till=0,
            rows_with_changed_cost=0,
            rows_with_changed_ean=0,
            applied_updates=0,
            failed_updates=0,
            skipped_unchanged=1,
        ),
        unmapped_rows=[
            {
                "supplier_code": 19999,
                "description": "Mystery Spirit",
                "size": "1L",
                "pack": 6,
                "price_pence": 2000,
                "ean": "9999999999999",
            },
        ],
        lines=["[UNMAPPED] Supplier 19999 | Mystery Spirit | 1L"],
    )

    body = format_supplier_update_report(report, apply=True)

    assert "Unmapped supplier codes" in body
    assert "19999 | Mystery Spirit | 1L" in body
    assert "Missing supplier codes: 1" in body


def test_format_supplier_update_report_includes_errors() -> None:
    report = SupplierUpdateReport(
        summary=SupplierUpdateSummary(
            parsed_rows=0,
            mapped_rows=0,
            missing_supplier_codes=0,
            ignored_supplier_rows=0,
            missing_plus_on_till=0,
            rows_with_changed_cost=0,
            rows_with_changed_ean=0,
            applied_updates=0,
            failed_updates=0,
            skipped_unchanged=0,
        ),
        errors=["No order rows were parsed from supplier confirmation."],
    )

    body = format_supplier_update_report(report, apply=False)

    assert "Errors:" in body
    assert "No order rows were parsed from supplier confirmation." in body


@patch("supplier_updates.get_loyverse_items")
@patch("supplier_updates.build_till_products")
def test_write_mapping_false_skips_yaml_mutation(
    mock_build_till_products,
    mock_get_loyverse_items,
) -> None:
    mock_get_loyverse_items.return_value = []
    mock_build_till_products.return_value = {}

    with tempfile.TemporaryDirectory() as temp_dir:
        mapping_file = Path(temp_dir) / "supplier_data.yaml"
        _write_mapping_file(mapping_file)
        original_text = mapping_file.read_text()

        with pytest.raises(ValueError, match="No order rows were parsed"):
            run_supplier_cost_updates(
                confirmation_text="Quantity Code Description Size Pack Price EAN code\n",
                mapping_file=mapping_file,
                apply=False,
                write_mapping=False,
                log_to_console=False,
            )

        assert mapping_file.read_text() == original_text


@patch("supplier_updates.get_loyverse_items")
@patch("supplier_updates.build_till_products")
def test_run_supplier_cost_updates_collects_unmapped_rows(
    mock_build_till_products,
    mock_get_loyverse_items,
) -> None:
    mock_get_loyverse_items.return_value = []
    mock_build_till_products.return_value = {}

    with tempfile.TemporaryDirectory() as temp_dir:
        mapping_file = Path(temp_dir) / "supplier_data.yaml"
        _write_mapping_file(mapping_file)

        report = run_supplier_cost_updates(
            confirmation_text=CONFIRMATION_TEXT,
            mapping_file=mapping_file,
            apply=False,
            write_mapping=False,
            log_to_console=False,
        )

    assert report.summary.parsed_rows == 2
    assert report.summary.missing_supplier_codes == 1
    assert len(report.unmapped_rows) == 1
    assert report.unmapped_rows[0]["supplier_code"] == 19999


@patch("supplier_updates.seed_missing_supplier_codes")
@patch("supplier_updates.update_supplier_data_comments")
@patch("supplier_updates.get_loyverse_items")
@patch("supplier_updates.build_till_products")
def test_write_mapping_true_updates_yaml(
    mock_build_till_products,
    mock_get_loyverse_items,
    mock_update_comments,
    mock_seed_codes,
) -> None:
    mock_get_loyverse_items.return_value = []
    mock_build_till_products.return_value = {}
    mock_seed_codes.return_value = 0

    with tempfile.TemporaryDirectory() as temp_dir:
        mapping_file = Path(temp_dir) / "supplier_data.yaml"
        _write_mapping_file(mapping_file)

        run_supplier_cost_updates(
            confirmation_text=CONFIRMATION_TEXT,
            mapping_file=mapping_file,
            apply=False,
            write_mapping=True,
            log_to_console=False,
        )

        mock_update_comments.assert_called_once()
        mock_seed_codes.assert_called_once()
        entries = get_supplier_code_entries(mapping_file)
        assert 10001 in entries
