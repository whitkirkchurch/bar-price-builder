from __future__ import annotations

from typing import TypedDict


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
