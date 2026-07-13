from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

import requests

if TYPE_CHECKING:
    from supplier_data import SupplierCodeMapEntry, SupplierCodePluMapping, SupplierRow

AIRTABLE_API_URL = "https://api.airtable.com/v0"
DEFAULT_TABLE_NAME = "Supplier mapping"
FIELD_SUPPLIER_CODE = "Supplier Code"
FIELD_LABEL = "Label"
FIELD_IGNORE = "Ignore"
FIELD_PLU = "PLU"
FIELD_SERVINGS_PER_UNIT = "Servings per Unit"


@dataclass(frozen=True)
class SupplierMappingRecord:
    record_id: str
    supplier_code: int
    label: str
    ignore: bool
    mapping: SupplierCodePluMapping | None


class AirtableSupplierMappingStore:
    def __init__(
        self,
        *,
        personal_access_token: str,
        base_id: str,
        table_name: str = DEFAULT_TABLE_NAME,
        session: requests.Session | None = None,
    ) -> None:
        self._personal_access_token = personal_access_token
        self._base_id = base_id
        self._table_name = table_name
        self._table_path = quote(table_name, safe="")
        self._session = session or requests.Session()

    @classmethod
    def from_env(cls) -> AirtableSupplierMappingStore:
        personal_access_token = os.getenv("AIRTABLE_PAT")
        if not personal_access_token:
            msg = "AIRTABLE_PAT environment variable is not set"
            raise ValueError(msg)

        base_id = os.getenv("AIRTABLE_BASE_ID")
        if not base_id:
            msg = "AIRTABLE_BASE_ID environment variable is not set"
            raise ValueError(msg)

        table_name = os.getenv("AIRTABLE_SUPPLIER_MAPPING_TABLE", DEFAULT_TABLE_NAME)
        return cls(
            personal_access_token=personal_access_token,
            base_id=base_id,
            table_name=table_name,
        )

    def get_entries(self) -> dict[int, SupplierCodeMapEntry]:
        return {record.supplier_code: _record_to_entry(record) for record in self._list_records()}

    def update_labels(self, rows: list[SupplierRow]) -> int:
        labels_by_code = {row["supplier_code"]: _row_label(row) for row in rows}
        updates: list[dict[str, Any]] = []

        for record in self._list_records():
            label = labels_by_code.get(record.supplier_code)
            if label is None or record.label == label:
                continue
            updates.append(
                {
                    "id": record.record_id,
                    "fields": {FIELD_LABEL: label},
                },
            )

        self._patch_records(updates)
        return len(updates)

    def seed_missing_codes(self, rows: list[SupplierRow]) -> list[SupplierRow]:
        existing_codes = {record.supplier_code for record in self._list_records()}
        rows_by_code = {row["supplier_code"]: row for row in rows}

        new_rows: list[SupplierRow] = []
        creates: list[dict[str, Any]] = []
        for supplier_code in sorted(rows_by_code):
            if supplier_code in existing_codes:
                continue
            row = rows_by_code[supplier_code]
            new_rows.append(row)
            creates.append(
                {
                    "fields": {
                        FIELD_SUPPLIER_CODE: _stringify_identifier(supplier_code),
                        FIELD_LABEL: _row_label(row),
                        FIELD_IGNORE: False,
                    },
                },
            )

        self._create_records(creates)
        return new_rows

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._personal_access_token}",
            "Content-Type": "application/json",
        }

    def _records_url(self) -> str:
        return f"{AIRTABLE_API_URL}/{self._base_id}/{self._table_path}"

    def _list_records(self) -> list[SupplierMappingRecord]:
        records: list[SupplierMappingRecord] = []
        offset: str | None = None

        while True:
            params: dict[str, str] = {"pageSize": "100"}
            if offset is not None:
                params["offset"] = offset

            response = self._session.get(
                self._records_url(),
                headers=self._headers(),
                params=params,
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()

            records.extend(_parse_record(record) for record in payload.get("records", []))

            offset = payload.get("offset")
            if offset is None:
                return records

    def _create_records(self, records: list[dict[str, Any]]) -> None:
        for chunk in _chunked(records, 10):
            response = self._session.post(
                self._records_url(),
                headers=self._headers(),
                json={"records": chunk},
                timeout=30,
            )
            response.raise_for_status()

    def _patch_records(self, records: list[dict[str, Any]]) -> None:
        for chunk in _chunked(records, 10):
            response = self._session.patch(
                self._records_url(),
                headers=self._headers(),
                json={"records": chunk},
                timeout=30,
            )
            response.raise_for_status()


def _stringify_identifier(value: int | str) -> str:
    return str(value).strip()


def _row_label(row: SupplierRow) -> str:
    return f"{row['description']} | {row['size']}"


def _parse_identifier(value: object, *, field_name: str) -> int:
    if value is None:
        msg = f"{field_name} is required"
        raise ValueError(msg)

    text = str(value).strip()
    if not text.isdigit():
        msg = f"{field_name} must be numeric: {value!r}"
        raise ValueError(msg)

    return int(text)


def _parse_optional_identifier(value: object) -> int | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    return _parse_identifier(text, field_name="PLU")


def _chunked[T](items: list[T], size: int) -> list[list[T]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _parse_record(record: dict[str, Any]) -> SupplierMappingRecord:
    fields = record.get("fields", {})
    supplier_code_raw = fields.get(FIELD_SUPPLIER_CODE)
    if supplier_code_raw is None:
        msg = f"Airtable record is missing {FIELD_SUPPLIER_CODE!r}: {record!r}"
        raise ValueError(msg)

    supplier_code = _parse_identifier(supplier_code_raw, field_name=FIELD_SUPPLIER_CODE)
    label = str(fields.get(FIELD_LABEL, "")).strip()
    ignore = bool(fields.get(FIELD_IGNORE, False))

    plu_raw = fields.get(FIELD_PLU)
    servings_raw = fields.get(FIELD_SERVINGS_PER_UNIT)
    mapping: SupplierCodePluMapping | None = None
    if plu_raw is not None and servings_raw is not None:
        plu = _parse_optional_identifier(plu_raw)
        if plu is None:
            return SupplierMappingRecord(
                record_id=str(record["id"]),
                supplier_code=supplier_code,
                label=label,
                ignore=ignore,
                mapping=None,
            )

        servings_per_unit = float(servings_raw)
        if servings_per_unit <= 0:
            msg = f"Servings per Unit must be > 0 for supplier code {supplier_code}, PLU {plu}"
            raise ValueError(msg)
        mapping = {
            "plu": plu,
            "servings_per_unit": servings_per_unit,
        }

    return SupplierMappingRecord(
        record_id=str(record["id"]),
        supplier_code=supplier_code,
        label=label,
        ignore=ignore,
        mapping=mapping,
    )


def _record_to_entry(record: SupplierMappingRecord) -> SupplierCodeMapEntry:
    return {
        "mapping": record.mapping,
        "ignore": record.ignore,
        "comment": record.label,
    }
