from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypedDict

import click
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

if TYPE_CHECKING:
    from pathlib import Path

YAML_RT = YAML(typ="rt")
YAML_RT.preserve_quotes = False
YAML_RT.explicit_start = True
YAML_RT.indent(mapping=2, sequence=4, offset=2)
YAML_RT.width = 80
YAML_RT.default_flow_style = False


class SupplierCodePluMapping(TypedDict):
    plu: int
    servings_per_unit: float


class SupplierCodeMapEntry(TypedDict):
    mapping: SupplierCodePluMapping | None
    ignore: bool
    comment: str


class SupplierRow(TypedDict):
    supplier_code: int
    description: str
    size: str
    pack: int
    price_pence: int
    ean: str


HEADER_LABELS = ["Quantity", "Code", "Description", "Size", "Pack", "Price", "EAN code"]


def _find_header_index(lines: list[str]) -> int:
    for index, line in enumerate(lines):
        if all(label in line for label in HEADER_LABELS):
            return index
    return -1


def _column_starts(header_line: str) -> list[int]:
    starts: list[int] = []
    cursor = 0
    for label in HEADER_LABELS:
        start = header_line.find(label, cursor)
        if start == -1:
            return []
        starts.append(start)
        cursor = start + len(label)
    return starts


def _column_boundaries(starts: list[int]) -> list[tuple[str, int, int | None]]:
    boundaries: list[tuple[str, int, int | None]] = []
    for index, label in enumerate(HEADER_LABELS):
        start = starts[index]
        end = starts[index + 1] if index + 1 < len(starts) else None
        boundaries.append((label, start, end))
    return boundaries


def _slice_fields(raw_line: str, boundaries: list[tuple[str, int, int | None]]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for label, start, end in boundaries:
        chunk = raw_line[start:end] if end is not None else raw_line[start:]
        fields[label] = chunk.strip()
    return fields


def _parse_price_pence(value: str) -> int | None:
    try:
        return round(float(value) * 100)
    except ValueError:
        return None


def _split_description_and_size(middle: list[str]) -> tuple[str, str]:
    if not middle:
        return "", ""
    if len(middle) == 1:
        return middle[0], ""

    if len(middle) >= 3 and middle[-2].upper() == "X":
        size = " ".join(middle[-3:])
        description = " ".join(middle[:-3])
        return description, size

    size = middle[-1]
    description = " ".join(middle[:-1])
    return description, size


def _parse_space_separated_row(raw_line: str) -> SupplierRow | None:
    parts = raw_line.split()
    if len(parts) < 7:
        return None

    quantity_text = parts[0]
    supplier_code_text = parts[1]
    if not quantity_text.isdigit() or not supplier_code_text.isdigit():
        return None
    if quantity_text == "0":
        return None

    pack_text = parts[-3]
    price_text = parts[-2]
    ean_text = parts[-1]
    if not pack_text.isdigit():
        return None

    price_pence = _parse_price_pence(price_text)
    if price_pence is None:
        return None

    description, size = _split_description_and_size(parts[2:-3])

    return {
        "supplier_code": int(supplier_code_text),
        "description": description,
        "size": size,
        "pack": int(pack_text),
        "price_pence": price_pence,
        "ean": ean_text,
    }


def _parse_supplier_row(fields: dict[str, str]) -> SupplierRow | None:
    supplier_code_text = fields["Code"]
    pack_text = fields["Pack"]
    price_text = fields["Price"]

    if not supplier_code_text.isdigit() or not pack_text.isdigit() or not price_text:
        return None

    price_pence = _parse_price_pence(price_text)
    if price_pence is None:
        return None

    ean_text = fields.get("EAN code", "").strip()

    return {
        "supplier_code": int(supplier_code_text),
        "description": fields["Description"],
        "size": fields["Size"],
        "pack": int(pack_text),
        "price_pence": price_pence,
        "ean": ean_text,
    }


def contains_supplier_confirmation_header(text: str) -> bool:
    return _find_header_index(text.splitlines()) != -1


def parse_supplier_confirmation_rows(text: str) -> list[SupplierRow]:
    parsed_rows: list[SupplierRow] = []
    lines = text.splitlines()

    header_index = _find_header_index(lines)
    if header_index == -1:
        message = "Supplier confirmation file must contain a header row with required column labels"
        raise ValueError(message)

    starts = _column_starts(lines[header_index])
    if not starts:
        return parsed_rows

    boundaries = _column_boundaries(starts)

    for raw_line in lines[header_index + 1 :]:
        if not raw_line.strip():
            continue

        parsed_row = _parse_supplier_row(_slice_fields(raw_line, boundaries))
        if parsed_row is None:
            parsed_row = _parse_space_separated_row(raw_line)
        if parsed_row is not None:
            parsed_rows.append(parsed_row)

    return parsed_rows


def _load_supplier_map(path: Path) -> list[Any]:
    with path.open() as mapping_file:
        loaded = YAML_RT.load(mapping_file)
    if loaded is None:
        return []

    if isinstance(loaded, dict):
        items = loaded.get("items")
        if items is None:
            return []
        if not isinstance(items, list):
            msg = "supplier_data.yaml 'items' must be a YAML array"
            raise ValueError(msg)  # noqa: TRY004
        return items

    msg = "supplier_data.yaml must be an object with an 'items' array"
    raise ValueError(msg)


def _dump_supplier_map(path: Path, data: list[Any]) -> None:
    with path.open("w") as mapping_file:
        YAML_RT.dump(CommentedMap({"items": data}), mapping_file)


def get_supplier_code_entries(path: Path) -> dict[int, SupplierCodeMapEntry]:
    raw_data = _load_supplier_map(path)

    entries_by_code: dict[int, SupplierCodeMapEntry] = {}

    for row in raw_data:
        if not isinstance(row, dict) or "supplier_code" not in row:
            msg = f"Each supplier mapping row must be an object with supplier_code: {row!r}"
            raise ValueError(msg)

        supplier_code = int(row["supplier_code"])
        mapping_value = row.get("mapping")

        normalized_mapping: SupplierCodePluMapping | None = None
        if mapping_value is not None:
            if not isinstance(mapping_value, dict):
                msg = f"mapping must be an object for supplier code {supplier_code}: {mapping_value!r}"
                raise ValueError(msg)
            if "plu" not in mapping_value or "servings_per_unit" not in mapping_value:
                msg = f"mapping must include plu and servings_per_unit for supplier code {supplier_code}"
                raise ValueError(msg)

            servings_per_unit = float(mapping_value["servings_per_unit"])
            if servings_per_unit <= 0:
                msg = f"servings_per_unit must be > 0 for supplier code {supplier_code}, PLU {mapping_value['plu']}"
                raise ValueError(msg)

            normalized_mapping = {
                "plu": int(mapping_value["plu"]),
                "servings_per_unit": servings_per_unit,
            }

        entries_by_code[supplier_code] = {
            "mapping": normalized_mapping,
            "ignore": bool(row.get("ignore", False)),
            "comment": str(row.get("comment", "")).strip(),
        }

    return entries_by_code


def get_supplier_data_mappings(path: Path) -> dict[int, SupplierCodePluMapping]:
    entries_by_code = get_supplier_code_entries(path)
    return get_active_mapping_by_code(entries_by_code)


def get_active_mapping_by_code(
    entries_by_code: dict[int, SupplierCodeMapEntry],
) -> dict[int, SupplierCodePluMapping]:
    mappings_by_code: dict[int, SupplierCodePluMapping] = {}
    for supplier_code, entry in entries_by_code.items():
        if entry["ignore"]:
            continue
        if entry["mapping"] is not None:
            mappings_by_code[supplier_code] = entry["mapping"]

    return mappings_by_code


def _sort_supplier_code_rows(rows: list) -> list:
    def sort_key(entry) -> tuple[int, int]:
        if isinstance(entry, dict) and "supplier_code" in entry:
            return (0, int(entry["supplier_code"]))
        return (1, 0)

    return sorted(rows, key=sort_key)


def _normalize_supplier_code_row(entry: dict) -> dict:
    normalized: CommentedMap = CommentedMap(
        {
            "supplier_code": int(entry["supplier_code"]),
            "comment": str(entry.get("comment", "")),
        },
    )

    if "ignore" in entry:
        normalized["ignore"] = bool(entry["ignore"])
    mapping_value = entry.get("mapping")
    if mapping_value is not None:
        normalized["mapping"] = mapping_value

    return normalized


def _normalize_and_sort_supplier_rows(rows: list) -> list:
    normalized_rows: list = []
    passthrough_rows: list = []

    for row in rows:
        if isinstance(row, dict) and "supplier_code" in row:
            normalized_rows.append(_normalize_supplier_code_row(row))
        else:
            passthrough_rows.append(row)

    return _sort_supplier_code_rows(normalized_rows) + passthrough_rows


def update_supplier_data_comments(path: Path, rows: list[SupplierRow]) -> None:
    raw_data = _load_supplier_map(path)

    comments_by_code = {row["supplier_code"]: f"{row['description']} | {row['size']}" for row in rows}

    for entry in raw_data:
        if not isinstance(entry, dict) or "supplier_code" not in entry:
            continue

        supplier_code = int(entry["supplier_code"])
        comment = comments_by_code.get(supplier_code)
        if not comment:
            continue

        if entry.get("comment") != comment:
            entry["comment"] = comment

    sorted_data = _normalize_and_sort_supplier_rows(raw_data)

    _dump_supplier_map(path, sorted_data)


def seed_missing_supplier_codes(path: Path, rows: list[SupplierRow]) -> int:
    """Append unseen supplier codes with a comment only for manual mapping later."""
    raw_data = _load_supplier_map(path)

    existing_codes = {
        int(entry["supplier_code"]) for entry in raw_data if isinstance(entry, dict) and "supplier_code" in entry
    }

    rows_by_code: dict[int, SupplierRow] = {}
    for row in rows:
        rows_by_code[row["supplier_code"]] = row

    added_count = 0
    for supplier_code in sorted(rows_by_code):
        if supplier_code in existing_codes:
            continue

        row = rows_by_code[supplier_code]
        raw_data.append(
            CommentedMap(
                {
                    "supplier_code": supplier_code,
                    "comment": f"{row['description']} | {row['size']}",
                },
            ),
        )
        existing_codes.add(supplier_code)
        added_count += 1

    if added_count == 0:
        return 0

    sorted_data = _normalize_and_sort_supplier_rows(raw_data)

    _dump_supplier_map(path, sorted_data)

    return added_count


def get_duplicate_plu_mapping_warnings(mappings_by_code: dict[int, SupplierCodePluMapping]) -> list[str]:
    plu_to_supplier_codes: dict[int, set[int]] = {}

    for supplier_code, mapping in mappings_by_code.items():
        plu = mapping["plu"]
        if plu not in plu_to_supplier_codes:
            plu_to_supplier_codes[plu] = set()
        plu_to_supplier_codes[plu].add(supplier_code)

    warnings: list[str] = []
    for plu, supplier_codes in sorted(plu_to_supplier_codes.items()):
        if len(supplier_codes) <= 1:
            continue

        codes = ", ".join(str(code) for code in sorted(supplier_codes))
        warnings.append(f"DUPLICATE MAPPING: PLU {plu} is mapped to multiple supplier codes: {codes}")

    return warnings


def warn_for_duplicate_plu_mappings(mappings_by_code: dict[int, SupplierCodePluMapping]) -> None:
    for warning in get_duplicate_plu_mapping_warnings(mappings_by_code):
        click.echo(click.style(warning, fg="yellow"))
