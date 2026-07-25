"""Tests for airtable_supplier_mapping.py."""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
import requests

from airtable_supplier_mapping import AirtableSupplierMappingStore

if TYPE_CHECKING:
    from supplier_data import SupplierRow

TABLE_ID = "tblTEST"


def _record(
    *,
    record_id: str,
    supplier_code: str | int,
    label: str = "",
    ignore: bool = False,
    plu: str | int | None = None,
    servings_per_unit: float | None = None,
) -> dict:
    fields: dict = {
        "Supplier Code": str(supplier_code),
        "Supplier Label": label,
        "Ignore": ignore,
    }
    if plu is not None:
        fields["PLU"] = str(plu)
    if servings_per_unit is not None:
        fields["Servings per Unit"] = servings_per_unit
    return {"id": record_id, "fields": fields}


def test_get_entries_normalizes_airtable_records() -> None:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {
        "records": [
            _record(
                record_id="rec1",
                supplier_code=10001,
                label="Vodka",
                plu=1001,
                servings_per_unit=1,
            ),
            _record(record_id="rec2", supplier_code=10002, label="Ignored", ignore=True),
        ],
    }
    response.raise_for_status.return_value = None
    session.get.return_value = response

    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )
    entries = store.get_entries()

    assert entries[10001]["mapping"] == {"plu": 1001, "servings_per_unit": 1.0}
    assert entries[10001]["comment"] == "Vodka"
    assert entries[10002]["ignore"] is True
    assert entries[10002]["mapping"] is None


def test_seed_missing_codes_creates_only_new_rows() -> None:
    session = MagicMock()
    list_response = MagicMock()
    list_response.json.return_value = {
        "records": [_record(record_id="rec1", supplier_code=10001, label="Vodka")],
    }
    list_response.raise_for_status.return_value = None
    create_response = MagicMock()
    create_response.json.return_value = {
        "records": [_record(record_id="recNew", supplier_code=19999, label="Mystery Spirit | 1L")],
    }
    create_response.raise_for_status.return_value = None
    session.get.return_value = list_response
    session.post.return_value = create_response

    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )
    new_rows = store.seed_missing_codes(
        [
            {
                "supplier_code": 10001,
                "description": "Vodka",
                "size": "1L",
                "pack": 6,
                "price_pence": 1250,
                "ean": "123",
            },
            {
                "supplier_code": 19999,
                "description": "Mystery Spirit",
                "size": "1L",
                "pack": 6,
                "price_pence": 2000,
                "ean": "999",
            },
        ],
        last_supplier_update=date(2026, 7, 17),
    )

    assert len(new_rows) == 1
    assert new_rows[0]["supplier_code"] == 19999
    session.post.assert_called_once()
    assert session.post.call_args.args[0] == "https://api.airtable.com/v0/appTEST/tblTEST"
    payload = session.post.call_args.kwargs["json"]
    assert payload["records"][0]["fields"]["Supplier Code"] == "19999"
    assert payload["records"][0]["fields"]["Supplier Label"] == "Mystery Spirit | 1L"
    assert payload["records"][0]["fields"]["Last supplier update"] == "2026-07-17"


def test_from_env_requires_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AIRTABLE_PAT", raising=False)
    monkeypatch.delenv("AIRTABLE_BASE_ID", raising=False)
    monkeypatch.delenv("AIRTABLE_SUPPLIER_MAPPING_TABLE_ID", raising=False)

    with pytest.raises(ValueError, match="AIRTABLE_PAT"):
        AirtableSupplierMappingStore.from_env()


def test_from_env_requires_table_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIRTABLE_PAT", "test-token")
    monkeypatch.setenv("AIRTABLE_BASE_ID", "appTEST")
    monkeypatch.delenv("AIRTABLE_SUPPLIER_MAPPING_TABLE_ID", raising=False)

    with pytest.raises(ValueError, match="AIRTABLE_SUPPLIER_MAPPING_TABLE_ID"):
        AirtableSupplierMappingStore.from_env()


def test_get_entries_accepts_string_supplier_code_and_plu() -> None:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {
        "records": [
            _record(
                record_id="rec1",
                supplier_code="10001",
                label="Vodka",
                plu="1001",
                servings_per_unit=1,
            ),
        ],
    }
    response.raise_for_status.return_value = None
    session.get.return_value = response

    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )
    entries = store.get_entries()

    assert entries[10001]["mapping"] == {"plu": 1001, "servings_per_unit": 1.0}


def test_get_entries_raises_for_invalid_servings_per_unit() -> None:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {
        "records": [
            _record(
                record_id="rec1",
                supplier_code=10001,
                plu=1001,
                servings_per_unit=0,
            ),
        ],
    }
    response.raise_for_status.return_value = None
    session.get.return_value = response

    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )

    with pytest.raises(ValueError, match="Servings per Unit must be > 0"):
        store.get_entries()


def test_update_labels_patches_changed_records_only() -> None:
    session = MagicMock()
    list_response = MagicMock()
    list_response.json.return_value = {
        "records": [_record(record_id="rec1", supplier_code=10001, label="Old label")],
    }
    list_response.raise_for_status.return_value = None
    patch_response = MagicMock()
    patch_response.raise_for_status.return_value = None
    session.get.return_value = list_response
    session.patch.return_value = patch_response

    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )
    updated = store.update_labels(
        [
            {
                "supplier_code": 10001,
                "description": "Vodka",
                "size": "1L",
                "pack": 6,
                "price_pence": 1250,
                "ean": "123",
            },
        ],
    )

    assert updated == 1
    session.patch.assert_not_called()

    flushed = store.flush_updates()

    assert flushed == 1
    session.patch.assert_called_once()
    payload = session.patch.call_args.kwargs["json"]
    assert payload["records"][0]["fields"]["Supplier Label"] == "Vodka | 1L"


def test_queue_last_supplier_updates_includes_every_seen_existing_product() -> None:
    session = MagicMock()
    list_response = MagicMock()
    list_response.json.return_value = {
        "records": [
            _record(record_id="rec1", supplier_code=10001, label="Vodka | 1L"),
            _record(record_id="rec2", supplier_code=10002, label="Ignored", ignore=True),
            _record(record_id="rec3", supplier_code=10003, label="Not in confirmation"),
        ],
    }
    list_response.raise_for_status.return_value = None
    patch_response = MagicMock()
    patch_response.raise_for_status.return_value = None
    session.get.return_value = list_response
    session.patch.return_value = patch_response
    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )
    rows: list[SupplierRow] = [
        {
            "supplier_code": supplier_code,
            "description": description,
            "size": "1L",
            "pack": 6,
            "price_pence": 1250,
            "ean": str(supplier_code),
        }
        for supplier_code, description in [(10001, "Vodka"), (10002, "Ignored")]
    ]

    queued = store.queue_last_supplier_updates(
        rows,
        updated_at=date(2026, 7, 17),
    )
    flushed = store.flush_updates()

    assert queued == 2
    assert flushed == 2
    payload = session.patch.call_args.kwargs["json"]
    assert payload["records"] == [
        {"id": "rec1", "fields": {"Last supplier update": "2026-07-17"}},
        {"id": "rec2", "fields": {"Last supplier update": "2026-07-17"}},
    ]


def test_queue_last_cost_changes_only_includes_changed_products() -> None:
    session = MagicMock()
    list_response = MagicMock()
    list_response.json.return_value = {
        "records": [
            _record(record_id="rec1", supplier_code=10001),
            _record(record_id="rec2", supplier_code=10002),
        ],
    }
    list_response.raise_for_status.return_value = None
    patch_response = MagicMock()
    patch_response.raise_for_status.return_value = None
    session.get.return_value = list_response
    session.patch.return_value = patch_response
    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )

    queued = store.queue_last_cost_changes(
        {10002},
        changed_at=date(2026, 7, 17),
    )
    flushed = store.flush_updates()

    assert queued == 1
    assert flushed == 1
    payload = session.patch.call_args.kwargs["json"]
    assert payload["records"] == [
        {"id": "rec2", "fields": {"Last cost change": "2026-07-17"}},
    ]


def test_flush_updates_merges_changed_fields_and_batches_records() -> None:
    session = MagicMock()
    patch_response = MagicMock()
    patch_response.raise_for_status.return_value = None
    session.patch.return_value = patch_response
    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )

    store.queue_record_update("rec0", {"Supplier Label": "Updated"})
    store.queue_record_update("rec0", {"New Field": "value"})
    for index in range(1, 12):
        store.queue_record_update(f"rec{index}", {"New Field": index})

    flushed = store.flush_updates()

    assert flushed == 12
    assert session.patch.call_count == 2
    first_payload = session.patch.call_args_list[0].kwargs["json"]
    second_payload = session.patch.call_args_list[1].kwargs["json"]
    assert len(first_payload["records"]) == 10
    assert len(second_payload["records"]) == 2
    assert first_payload["records"][0] == {
        "id": "rec0",
        "fields": {"Supplier Label": "Updated", "New Field": "value"},
    }


def test_flush_updates_does_nothing_when_no_changes_are_queued() -> None:
    session = MagicMock()
    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )

    assert store.flush_updates() == 0
    session.patch.assert_not_called()


def test_list_records_raises_for_http_errors() -> None:
    session = MagicMock()
    response = MagicMock()
    response.raise_for_status.side_effect = requests.HTTPError("boom")
    session.get.return_value = response

    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )

    with pytest.raises(requests.HTTPError):
        store.get_entries()


def test_get_record_urls_by_code_builds_airtable_links() -> None:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {
        "records": [
            _record(
                record_id="rec1",
                supplier_code=10001,
                label="Vodka",
                plu=1001,
                servings_per_unit=1,
            ),
        ],
    }
    response.raise_for_status.return_value = None
    session.get.return_value = response

    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )

    assert store.get_record_urls_by_code() == {
        10001: "https://airtable.com/appTEST/tblTEST/rec1",
    }
    assert store.get_table_url() == "https://airtable.com/appTEST/tblTEST"


def test_list_records_is_cached_for_the_lifetime_of_the_store() -> None:
    session = MagicMock()
    response = MagicMock()
    response.json.return_value = {
        "records": [_record(record_id="rec1", supplier_code=10001, label="Vodka")],
    }
    response.raise_for_status.return_value = None
    session.get.return_value = response

    store = AirtableSupplierMappingStore(
        personal_access_token="test-token",
        base_id="appTEST",
        table_id=TABLE_ID,
        session=session,
    )

    store.get_entries()
    store.get_record_urls_by_code()
    store.update_labels(
        [
            {
                "supplier_code": 10001,
                "description": "Vodka",
                "size": "1L",
                "pack": 6,
                "price_pence": 1250,
                "ean": "123",
            },
        ],
    )

    session.get.assert_called_once()
