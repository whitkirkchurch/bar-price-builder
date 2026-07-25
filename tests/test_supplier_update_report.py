"""Tests for supplier update report formatting and mapping behaviour."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app import _print_supplier_update_summary
from supplier_updates import (
    RowProcessResult,
    SupplierUpdateReport,
    SupplierUpdateSummary,
    format_supplier_update_report,
    run_supplier_cost_updates,
)

CONFIRMATION_TEXT = """
Delivery date:17/07/26
Quantity Code        Description      Size Pack Price   EAN code
1        10001       Vodka            1L   6    12.50   1234567890123
2        19999       Mystery Spirit   1L   6    20.00   9999999999999
"""


def _mapping_store() -> MagicMock:
    store = MagicMock()
    store.get_entries.return_value = {
        10001: {
            "mapping": {"plu": 1001, "servings_per_unit": 1},
            "ignore": False,
            "comment": "Vodka",
        },
    }
    store.update_labels.return_value = 0
    store.queue_last_supplier_updates.return_value = 1
    store.seed_missing_codes.return_value = []
    store.get_record_urls_by_code.return_value = {
        10001: "https://airtable.com/appTEST/tblTEST/rec1",
        19999: "https://airtable.com/appTEST/tblTEST/recNew",
    }
    store.get_table_url.return_value = "https://airtable.com/appTEST/tblTEST"
    return store


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

    body = format_supplier_update_report(report)

    assert "Unmapped supplier codes (add PLU mapping in Airtable)" in body
    assert "19999 | Mystery Spirit | 1L" in body
    assert "Missing supplier codes: 1" in body
    assert "API updates" not in body


def test_format_supplier_update_report_includes_airtable_links() -> None:
    record_url = "https://airtable.com/appTEST/tblTEST/recNew"
    table_url = "https://airtable.com/appTEST/tblTEST"
    report = SupplierUpdateReport(
        summary=SupplierUpdateSummary(
            parsed_rows=1,
            mapped_rows=0,
            missing_supplier_codes=1,
            ignored_supplier_rows=0,
            missing_plus_on_till=0,
            rows_with_changed_cost=0,
            rows_with_changed_ean=0,
            applied_updates=0,
            failed_updates=0,
            skipped_unchanged=0,
        ),
        newly_seeded_rows=[
            {
                "supplier_code": 19999,
                "description": "Mystery Spirit",
                "size": "1L",
                "pack": 6,
                "price_pence": 2000,
                "ean": "9999999999999",
            },
        ],
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
        airtable_table_url=table_url,
        airtable_record_urls_by_code={19999: record_url},
        lines=[f"[UNMAPPED] Supplier 19999 | Mystery Spirit | 1L — {record_url}"],
    )

    body = format_supplier_update_report(report)

    assert record_url in body
    assert "Please update new supplier codes in Airtable with PLU and servings data:" in body
    assert table_url in body


def test_format_supplier_update_report_includes_newly_seeded_products() -> None:
    report = SupplierUpdateReport(
        summary=SupplierUpdateSummary(
            parsed_rows=1,
            mapped_rows=0,
            missing_supplier_codes=1,
            ignored_supplier_rows=0,
            missing_plus_on_till=0,
            rows_with_changed_cost=0,
            rows_with_changed_ean=0,
            applied_updates=0,
            failed_updates=0,
            skipped_unchanged=0,
        ),
        newly_seeded_rows=[
            {
                "supplier_code": 19999,
                "description": "Mystery Spirit",
                "size": "1L",
                "pack": 6,
                "price_pence": 2000,
                "ean": "9999999999999",
            },
        ],
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
    )

    body = format_supplier_update_report(report)

    assert "New supplier products seeded in Airtable (add PLU mapping)" in body
    assert "19999 | Mystery Spirit | 1L" in body
    assert "Unmapped supplier codes" not in body


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

    body = format_supplier_update_report(report)

    assert "Errors:" in body
    assert "No order rows were parsed from supplier confirmation." in body


def test_cli_summary_omits_api_update_feedback(capsys: pytest.CaptureFixture[str]) -> None:
    report = SupplierUpdateReport(
        summary=SupplierUpdateSummary(
            parsed_rows=2,
            mapped_rows=1,
            missing_supplier_codes=1,
            ignored_supplier_rows=0,
            missing_plus_on_till=0,
            rows_with_changed_cost=1,
            rows_with_changed_ean=0,
            applied_updates=1,
            failed_updates=1,
            skipped_unchanged=1,
        ),
    )

    _print_supplier_update_summary(report)

    assert "API updates" not in capsys.readouterr().out


@patch("supplier_updates.get_loyverse_items")
@patch("supplier_updates.build_till_products")
def test_write_mapping_false_skips_airtable_writes(
    mock_build_till_products,
    mock_get_loyverse_items,
) -> None:
    mock_get_loyverse_items.return_value = []
    mock_build_till_products.return_value = {}
    store = _mapping_store()

    with pytest.raises(ValueError, match="No order rows were parsed"):
        run_supplier_cost_updates(
            confirmation_text="Quantity Code Description Size Pack Price EAN code\n",
            mapping_store=store,
            apply=False,
            write_mapping=False,
        )

    store.update_labels.assert_not_called()
    store.queue_last_supplier_updates.assert_not_called()
    store.queue_last_cost_changes.assert_not_called()
    store.seed_missing_codes.assert_not_called()
    store.flush_updates.assert_not_called()


def test_run_supplier_cost_updates_requires_delivery_date() -> None:
    store = _mapping_store()
    confirmation_text = """
Quantity Code Description Size Pack Price EAN code
1 10001 Vodka 1L 6 12.50 1234567890123
"""

    with pytest.raises(ValueError, match="must contain a delivery date"):
        run_supplier_cost_updates(
            confirmation_text=confirmation_text,
            mapping_store=store,
            apply=False,
        )

    store.update_labels.assert_not_called()


@patch("supplier_updates.get_loyverse_items")
@patch("supplier_updates.build_till_products")
def test_run_supplier_cost_updates_collects_unmapped_rows(
    mock_build_till_products,
    mock_get_loyverse_items,
) -> None:
    mock_get_loyverse_items.return_value = []
    mock_build_till_products.return_value = {}
    store = _mapping_store()

    report = run_supplier_cost_updates(
        confirmation_text=CONFIRMATION_TEXT,
        mapping_store=store,
        apply=False,
        write_mapping=False,
    )

    assert report.summary.parsed_rows == 2
    assert report.summary.missing_supplier_codes == 1
    assert len(report.unmapped_rows) == 1
    assert report.unmapped_rows[0]["supplier_code"] == 19999


@patch("supplier_updates._process_single_row")
@patch("supplier_updates.get_loyverse_items")
@patch("supplier_updates.build_till_products")
def test_write_mapping_true_updates_airtable(
    mock_build_till_products,
    mock_get_loyverse_items,
    mock_process_single_row,
) -> None:
    mock_get_loyverse_items.return_value = []
    mock_build_till_products.return_value = {}
    store = _mapping_store()
    store.seed_missing_codes.return_value = [
        {
            "supplier_code": 19999,
            "description": "Mystery Spirit",
            "size": "1L",
            "pack": 6,
            "price_pence": 2000,
            "ean": "9999999999999",
        },
    ]
    mock_process_single_row.side_effect = [
        RowProcessResult(True, 1, 0, 0, 0, 1, 0, 0, 0, 1),
        RowProcessResult(True, 1, 0, 0, 0, 0, 0, 0, 0, 1),
    ]

    report = run_supplier_cost_updates(
        confirmation_text=CONFIRMATION_TEXT,
        mapping_store=store,
        apply=False,
        write_mapping=True,
    )

    store.update_labels.assert_called_once()
    store.queue_last_supplier_updates.assert_called_once()
    store.seed_missing_codes.assert_called_once()
    assert store.flush_updates.call_count == 2
    updated_at = store.queue_last_supplier_updates.call_args.kwargs["updated_at"]
    assert updated_at == date(2026, 7, 17)
    assert store.seed_missing_codes.call_args.kwargs["last_supplier_update"] is updated_at
    store.queue_last_cost_changes.assert_called_once_with(
        {10001},
        changed_at=updated_at,
    )
    assert len(report.newly_seeded_rows) == 1
    assert report.newly_seeded_rows[0]["supplier_code"] == 19999
