from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import click

from loyverse import TillProduct, build_till_products, get_loyverse_items, update_loyverse_cost
from supplier_data import (
    SupplierCodeMapEntry,
    SupplierCodePluMapping,
    SupplierRow,
    get_active_mapping_by_code,
    get_supplier_code_entries,
    parse_supplier_confirmation_rows,
    seed_missing_supplier_codes,
    update_supplier_data_comments,
    warn_for_duplicate_plu_mappings,
)


@dataclass
class SupplierUpdateSummary:
    parsed_rows: int
    mapped_rows: int
    missing_supplier_codes: int
    ignored_supplier_rows: int
    missing_plus_on_till: int
    rows_with_changed_cost: int
    rows_with_changed_ean: int
    applied_updates: int
    failed_updates: int
    skipped_unchanged: int


@dataclass
class RowChangeContext:
    cost_changed: bool
    ean_changed: bool
    cost_per_serving_pounds: float
    current_cost_pounds: float | None
    current_ean: str | None
    supplier_ean: str | None
    ean_update_allowed: bool
    servings_per_unit: float


@dataclass
class CostUpdateRequest:
    apply: bool
    changed: bool
    till_product: TillProduct
    items_by_id: dict[str, dict]
    plu: int
    cost_per_serving_pounds: float
    new_barcode: str | None


@dataclass
class RowProcessResult:
    processed: bool
    mapped_count: int
    missing_code_count: int
    ignored_count: int
    missing_plu_count: int
    cost_changed_count: int
    ean_changed_count: int
    updated_count: int
    failed_count: int
    skipped_count: int


@dataclass
class ProcessingContext:
    entries_by_code: dict[int, SupplierCodeMapEntry]
    mappings_by_code: dict[int, SupplierCodePluMapping]
    till_products: dict[int, TillProduct]
    items_by_id: dict[str, dict]
    apply: bool


def round_loyverse_cost_pounds(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def has_cost_changed(current_cost_pence: float | None, new_cost_pounds: float) -> bool:
    if current_cost_pence is None:
        return True

    current_cost_pounds = round_loyverse_cost_pounds(current_cost_pence / 100)
    return current_cost_pounds != new_cost_pounds


def _normalize_ean(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def has_ean_changed(current_ean: str | None, new_ean: str | None) -> bool:
    return _normalize_ean(current_ean) != _normalize_ean(new_ean)


def _calculate_row_costs(row: SupplierRow, mapping: SupplierCodePluMapping) -> tuple[float, float, float]:
    unit_cost_pence = row["price_pence"] / row["pack"]
    cost_per_serving_pence = unit_cost_pence / mapping["servings_per_unit"]
    cost_per_serving_pounds = round_loyverse_cost_pounds(cost_per_serving_pence / 100)
    return unit_cost_pence, cost_per_serving_pence, cost_per_serving_pounds


def _apply_single_cost_update(request: CostUpdateRequest) -> tuple[str, str]:
    if not request.apply:
        return "dry-run", ""

    if not request.changed:
        return "unchanged", ""

    item_snapshot = request.items_by_id.get(request.till_product["item_id"])
    if item_snapshot is None:
        return "failed", f"Item snapshot not found for item_id {request.till_product['item_id']}"

    ok, response_message = update_loyverse_cost(
        item_snapshot,
        request.till_product["variant_id"],
        request.plu,
        request.cost_per_serving_pounds,
        request.new_barcode,
    )
    if ok:
        return "updated", ""

    return "failed", response_message


def _apply_successful_variant_update_to_snapshot(
    item_snapshot: dict,
    variant_id: str,
    cost_per_serving_pounds: float,
    new_barcode: str | None,
) -> None:
    for variant in item_snapshot.get("variants", []):
        current_variant_id = str(variant.get("variant_id", variant.get("id", "")))
        if current_variant_id != variant_id:
            continue

        variant["cost"] = cost_per_serving_pounds
        if new_barcode is not None:
            variant["barcode"] = new_barcode
        return


def _process_supplier_row(
    row: SupplierRow,
    mapping: SupplierCodePluMapping,
    till_product: TillProduct,
    items_by_id: dict[str, dict],
) -> RowChangeContext:
    _unit_cost_pence, _cost_per_serving_pence, cost_per_serving_pounds = _calculate_row_costs(row, mapping)
    current_cost_pence = till_product["cost_pence"]
    current_cost_pounds = (
        round_loyverse_cost_pounds(current_cost_pence / 100) if current_cost_pence is not None else None
    )
    cost_changed = has_cost_changed(current_cost_pence, cost_per_serving_pounds)

    supplier_ean = _normalize_ean(row["ean"])
    current_ean = _normalize_ean(till_product["barcode"])
    ean_update_allowed = mapping["servings_per_unit"] == 1
    ean_changed = ean_update_allowed and supplier_ean is not None and has_ean_changed(current_ean, supplier_ean)

    return RowChangeContext(
        cost_changed=cost_changed,
        ean_changed=ean_changed,
        cost_per_serving_pounds=cost_per_serving_pounds,
        current_cost_pounds=current_cost_pounds,
        current_ean=current_ean,
        supplier_ean=supplier_ean,
        ean_update_allowed=ean_update_allowed,
        servings_per_unit=mapping["servings_per_unit"],
    )


def _log_cost_line(
    row: SupplierRow,
    plu: int,
    item_name: str,
    new_cost_pounds: float,
    context: RowChangeContext,
) -> None:
    marker = "CHANGED" if (context.cost_changed or context.ean_changed) else "UNCHANGED"
    current_display = f"{context.current_cost_pounds:.2f}" if context.current_cost_pounds is not None else "unknown"

    cost_display = (
        f"cost £{current_display} -> £{new_cost_pounds:.2f}" if context.cost_changed else f"cost £{current_display}"
    )

    current_ean_display = _normalize_ean(context.current_ean) or "none"
    supplier_ean_display = _normalize_ean(context.supplier_ean)

    if not context.ean_update_allowed:
        if supplier_ean_display is None:
            ean_display = f"EAN {current_ean_display} (no supplier EAN)"
        else:
            ean_display = f"EAN {current_ean_display} (skipped: servings_per_unit={context.servings_per_unit:g})"
    elif context.ean_changed:
        ean_display = f"EAN {current_ean_display} -> {supplier_ean_display}"
    else:
        ean_display = f"EAN {current_ean_display}"

    line = f"[{marker}] Supplier {row['supplier_code']} | PLU {plu} {item_name} | {cost_display} | {ean_display}"
    if context.cost_changed or context.ean_changed:
        click.echo(click.style(line, fg="green"))
        return
    click.echo(line)


def _log_ignored_supplier_row(row: SupplierRow, entry: SupplierCodeMapEntry) -> None:
    comment = entry["comment"] or f"{row['description']} | {row['size']}"
    click.echo(click.style(f"[IGNORED] Supplier {row['supplier_code']} | {comment}", fg="cyan"))


def _log_unmapped_supplier_row(row: SupplierRow) -> None:
    click.echo(
        click.style(
            f"[UNMAPPED] Supplier {row['supplier_code']} | {row['description']} | {row['size']}",
            fg="yellow",
        ),
    )


def _process_single_row(
    row: SupplierRow,
    context: ProcessingContext,
) -> RowProcessResult:
    """Process a single supplier row and return aggregated counts."""
    supplier_code = row["supplier_code"]
    entry = context.entries_by_code.get(supplier_code)
    if entry is not None and entry["ignore"]:
        _log_ignored_supplier_row(row, entry)
        return RowProcessResult(False, 0, 0, 1, 0, 0, 0, 0, 0, 0)

    mapping = context.mappings_by_code.get(supplier_code)
    if mapping is None:
        _log_unmapped_supplier_row(row)
        return RowProcessResult(False, 0, 1, 0, 0, 0, 0, 0, 0, 0)

    if row["pack"] <= 0:
        click.echo(click.style(f"Supplier code {supplier_code} has invalid pack size {row['pack']}", fg="yellow"))
        return RowProcessResult(False, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    plu = mapping["plu"]
    till_product = context.till_products.get(plu)
    if till_product is None:
        click.echo(
            click.style(f"PLU {plu} mapped from supplier code {supplier_code} not found on till", fg="yellow"),
        )
        return RowProcessResult(False, 0, 0, 0, 1, 0, 0, 0, 0, 0)

    change_context = _process_supplier_row(row, mapping, till_product, context.items_by_id)
    changed = change_context.cost_changed or change_context.ean_changed
    new_barcode = change_context.supplier_ean if change_context.ean_changed else None
    item_snapshot = context.items_by_id.get(till_product["item_id"])

    request = CostUpdateRequest(
        apply=context.apply,
        changed=changed,
        till_product=till_product,
        items_by_id=context.items_by_id,
        plu=plu,
        cost_per_serving_pounds=change_context.cost_per_serving_pounds,
        new_barcode=new_barcode,
    )

    update_status, _update_error = _apply_single_cost_update(request)
    if update_status == "updated" and item_snapshot is not None:
        _apply_successful_variant_update_to_snapshot(
            item_snapshot,
            till_product["variant_id"],
            change_context.cost_per_serving_pounds,
            new_barcode,
        )

    _log_cost_line(row, plu, till_product["name"], change_context.cost_per_serving_pounds, change_context)

    status_counts = {
        "unchanged": (0, 0, 1),
        "updated": (1, 0, 0),
        "failed": (0, 1, 0),
        "dry-run": (0, 0, 1),
    }
    updated, failed, skipped = status_counts.get(update_status, (0, 0, 1))

    return RowProcessResult(
        processed=True,
        mapped_count=1,
        missing_code_count=0,
        ignored_count=0,
        missing_plu_count=0,
        cost_changed_count=1 if change_context.cost_changed else 0,
        ean_changed_count=1 if change_context.ean_changed else 0,
        updated_count=updated,
        failed_count=failed,
        skipped_count=skipped,
    )


def run_supplier_cost_updates(
    supplier_confirmation_file: Path,
    mapping_file: Path,
    apply: bool,
) -> SupplierUpdateSummary:
    click.echo("Reading supplier confirmation…")
    rows = parse_supplier_confirmation_rows(supplier_confirmation_file.read_text())
    if not rows:
        msg = "No order rows were parsed from file."
        raise ValueError(msg)

    update_supplier_data_comments(mapping_file, rows)
    seeded_codes = seed_missing_supplier_codes(mapping_file, rows)
    if seeded_codes > 0:
        click.echo(
            click.style(
                f"Added {seeded_codes} new supplier code entr{'y' if seeded_codes == 1 else 'ies'} to {mapping_file}",
                fg="green",
            ),
        )
    entries_by_code = get_supplier_code_entries(mapping_file)
    mappings_by_code = get_active_mapping_by_code(entries_by_code)
    warn_for_duplicate_plu_mappings(mappings_by_code)

    click.echo("Fetching products from Loyverse…")
    items = get_loyverse_items()
    till_products = build_till_products(items)
    items_by_id = {str(item.get("id", "")): item for item in items}

    processing_ctx = ProcessingContext(
        entries_by_code=entries_by_code,
        mappings_by_code=mappings_by_code,
        till_products=till_products,
        items_by_id=items_by_id,
        apply=apply,
    )

    mapped_rows = 0
    missing_codes = 0
    ignored_rows = 0
    missing_plus = 0
    updated = 0
    failed = 0
    skipped = 0
    changed_costs = 0
    changed_eans = 0

    for row in rows:
        result = _process_single_row(row, processing_ctx)
        if result.processed:
            mapped_rows += result.mapped_count
            changed_costs += result.cost_changed_count
            changed_eans += result.ean_changed_count
            updated += result.updated_count
            failed += result.failed_count
            skipped += result.skipped_count
        else:
            missing_codes += result.missing_code_count
            ignored_rows += result.ignored_count
            missing_plus += result.missing_plu_count

    return SupplierUpdateSummary(
        parsed_rows=len(rows),
        mapped_rows=mapped_rows,
        missing_supplier_codes=missing_codes,
        ignored_supplier_rows=ignored_rows,
        missing_plus_on_till=missing_plus,
        rows_with_changed_cost=changed_costs,
        rows_with_changed_ean=changed_eans,
        applied_updates=updated,
        failed_updates=failed,
        skipped_unchanged=skipped,
    )


def print_supplier_update_summary(summary: SupplierUpdateSummary, apply: bool) -> None:
    click.echo(f"Parsed supplier rows: {summary.parsed_rows}")
    click.echo(f"Mapped output rows: {summary.mapped_rows}")
    click.echo(f"Missing supplier codes: {summary.missing_supplier_codes}")
    click.echo(f"Ignored supplier rows: {summary.ignored_supplier_rows}")
    click.echo(f"Missing PLUs on till: {summary.missing_plus_on_till}")
    click.echo(f"Rows with changed cost: {summary.rows_with_changed_cost}")
    click.echo(f"Rows with changed EAN: {summary.rows_with_changed_ean}")
    if apply:
        click.echo(f"API updates applied: {summary.applied_updates}")
        click.echo(f"API updates failed: {summary.failed_updates}")
        click.echo(f"API updates skipped (unchanged): {summary.skipped_unchanged}")
