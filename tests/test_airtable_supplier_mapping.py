"""Tests for airtable_supplier_mapping.py."""

from unittest.mock import MagicMock

import pytest
import requests

from airtable_supplier_mapping import AirtableSupplierMappingStore

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
        "Label": label,
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
    )

    assert len(new_rows) == 1
    assert new_rows[0]["supplier_code"] == 19999
    session.post.assert_called_once()
    assert session.post.call_args.args[0] == "https://api.airtable.com/v0/appTEST/tblTEST"
    payload = session.post.call_args.kwargs["json"]
    assert payload["records"][0]["fields"]["Supplier Code"] == "19999"
    assert payload["records"][0]["fields"]["Label"] == "Mystery Spirit | 1L"


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
    session.patch.assert_called_once()
    payload = session.patch.call_args.kwargs["json"]
    assert payload["records"][0]["fields"]["Label"] == "Vodka | 1L"


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
