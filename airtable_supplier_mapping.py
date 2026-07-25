from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from datetime import date

    from supplier_data import SupplierCodeMapEntry, SupplierCodePluMapping, SupplierRow

AIRTABLE_API_URL = "https://api.airtable.com/v0"
AIRTABLE_WEB_BASE_URL = "https://airtable.com"
FIELD_SUPPLIER_CODE = "Supplier Code"
FIELD_LABEL = "Supplier Label"
FIELD_IGNORE = "Ignore"
FIELD_PLU = "PLU"
FIELD_SERVINGS_PER_UNIT = "Servings per Unit"
FIELD_LAST_SUPPLIER_UPDATE = "Last supplier update"
FIELD_LAST_COST_CHANGE = "Last cost change"


@dataclass(frozen=True)
class SupplierMappingRecord:
    record_id: str
    supplier_code: int
    label: str
    ignore: bool
    mapping: SupplierCodePluMapping | None


def build_table_url(base_id: str, table_id: str) -> str:
    return f"{AIRTABLE_WEB_BASE_URL}/{base_id}/{table_id}"


def build_record_url(base_id: str, table_id: str, record_id: str) -> str:
    return f"{AIRTABLE_WEB_BASE_URL}/{base_id}/{table_id}/{record_id}"


class AirtableSupplierMappingStore:
    def __init__(
        self,
        *,
        personal_access_token: str,
        base_id: str,
        table_id: str,
        session: requests.Session | None = None,
    ) -> None:
        self._personal_access_token = personal_access_token
        self._base_id = base_id
        self._table_id = table_id
        self._session = session or requests.Session()
        self._records_cache: list[SupplierMappingRecord] | None = None
        self._pending_updates_by_id: dict[str, dict[str, Any]] = {}

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

        table_id = os.getenv("AIRTABLE_SUPPLIER_MAPPING_TABLE_ID")
        if not table_id:
            msg = "AIRTABLE_SUPPLIER_MAPPING_TABLE_ID environment variable is not set"
            raise ValueError(msg)

        return cls(
            personal_access_token=personal_access_token,
            base_id=base_id,
            table_id=table_id,
        )

    def get_table_url(self) -> str:
        return build_table_url(self._base_id, self._table_id)

    def get_record_urls_by_code(self) -> dict[int, str]:
        return {
            record.supplier_code: build_record_url(self._base_id, self._table_id, record.record_id)
            for record in self._list_records()
        }

    def get_entries(self) -> dict[int, SupplierCodeMapEntry]:
        return {record.supplier_code: _record_to_entry(record) for record in self._list_records()}

    def update_labels(self, rows: list[SupplierRow]) -> int:
        labels_by_code = {row["supplier_code"]: _row_label(row) for row in rows}
        updated = 0

        for record in self._list_records():
            label = labels_by_code.get(record.supplier_code)
            if label is None or record.label == label:
                continue
            self.queue_record_update(record.record_id, {FIELD_LABEL: label})
            updated += 1

        return updated

    def queue_last_supplier_updates(self, rows: list[SupplierRow], *, updated_at: date) -> int:
        supplier_codes = {row["supplier_code"] for row in rows}
        return self._queue_timestamp_for_supplier_codes(
            supplier_codes,
            field_name=FIELD_LAST_SUPPLIER_UPDATE,
            timestamp=updated_at,
        )

    def queue_last_cost_changes(self, supplier_codes: set[int], *, changed_at: date) -> int:
        return self._queue_timestamp_for_supplier_codes(
            supplier_codes,
            field_name=FIELD_LAST_COST_CHANGE,
            timestamp=changed_at,
        )

    def _queue_timestamp_for_supplier_codes(
        self,
        supplier_codes: set[int],
        *,
        field_name: str,
        timestamp: date,
    ) -> int:
        formatted_timestamp = timestamp.isoformat()
        updated = 0

        for record in self._list_records():
            if record.supplier_code not in supplier_codes:
                continue
            self.queue_record_update(
                record.record_id,
                {field_name: formatted_timestamp},
            )
            updated += 1

        return updated

    def queue_record_update(self, record_id: str, fields: dict[str, Any]) -> None:
        if not fields:
            return

        pending_fields = self._pending_updates_by_id.setdefault(record_id, {})
        pending_fields.update(fields)

    def flush_updates(self) -> int:
        records = [{"id": record_id, "fields": fields} for record_id, fields in self._pending_updates_by_id.items()]
        if not records:
            return 0

        self._patch_records(records)
        self._pending_updates_by_id.clear()
        return len(records)

    def seed_missing_codes(
        self,
        rows: list[SupplierRow],
        *,
        last_supplier_update: date,
    ) -> list[SupplierRow]:
        existing_codes = {record.supplier_code for record in self._list_records()}
        rows_by_code = {row["supplier_code"]: row for row in rows}
        formatted_updated_at = last_supplier_update.isoformat()

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
                        FIELD_LAST_SUPPLIER_UPDATE: formatted_updated_at,
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
        return f"{AIRTABLE_API_URL}/{self._base_id}/{self._table_id}"

    def _list_records(self) -> list[SupplierMappingRecord]:
        if self._records_cache is not None:
            return self._records_cache

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
                self._records_cache = records
                return records

    def _create_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        created_records: list[dict[str, Any]] = []
        for chunk in _chunked(records, 10):
            response = self._session.post(
                self._records_url(),
                headers=self._headers(),
                json={"records": chunk},
                timeout=30,
            )
            response.raise_for_status()
            created_records.extend(response.json().get("records", []))

        if self._records_cache is not None:
            self._records_cache.extend(_parse_record(record) for record in created_records)

        return created_records

    def _patch_records(self, records: list[dict[str, Any]]) -> None:
        if not records:
            return

        for chunk in _chunked(records, 10):
            response = self._session.patch(
                self._records_url(),
                headers=self._headers(),
                json={"records": chunk},
                timeout=30,
            )
            response.raise_for_status()

        if self._records_cache is not None:
            self._apply_patches_to_cache(records)

    def _apply_patches_to_cache(self, records: list[dict[str, Any]]) -> None:
        if self._records_cache is None:
            return

        labels_by_id = {
            record["id"]: str(record["fields"][FIELD_LABEL]) for record in records if FIELD_LABEL in record["fields"]
        }
        if not labels_by_id:
            return

        self._records_cache = [
            replace(record, label=labels_by_id[record.record_id]) if record.record_id in labels_by_id else record
            for record in self._records_cache
        ]


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
