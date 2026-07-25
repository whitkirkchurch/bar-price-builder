from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from loyverse import TillProduct, build_till_products, get_loyverse_items, update_loyverse_cost
from supplier_data import (
    SupplierCodeMapEntry,
    SupplierCodePluMapping,
    SupplierRow,
    get_active_mapping_by_code,
    parse_supplier_confirmation_delivery_date,
    parse_supplier_confirmation_rows,
)

if TYPE_CHECKING:
    from datetime import date

    from airtable_supplier_mapping import AirtableSupplierMappingStore


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
class SupplierUpdateReport:
    summary: SupplierUpdateSummary
    lines: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unmapped_rows: list[SupplierRow] = field(default_factory=list)
    newly_seeded_rows: list[SupplierRow] = field(default_factory=list)
    ignored_rows: list[tuple[SupplierRow, str]] = field(default_factory=list)
    missing_plu_rows: list[tuple[int, int]] = field(default_factory=list)
    failed_update_messages: list[str] = field(default_factory=list)
    airtable_table_url: str | None = None
    airtable_record_urls_by_code: dict[int, str] = field(default_factory=dict)


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
    report: SupplierUpdateReport
    airtable_record_urls_by_code: dict[int, str]


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


def _emit(context: ProcessingContext, line: str, *, style: str | None = None) -> None:
    del style
    context.report.lines.append(line)


def _append_airtable_record_url(line: str, supplier_code: int, record_urls_by_code: dict[int, str]) -> str:
    url = record_urls_by_code.get(supplier_code)
    if url is None:
        return line
    return f"{line} — {url}"


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


def _format_cost_line(
    row: SupplierRow,
    plu: int,
    item_name: str,
    new_cost_pounds: float,
    context: RowChangeContext,
) -> str:
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

    return f"[{marker}] Supplier {row['supplier_code']} | PLU {plu} {item_name} | {cost_display} | {ean_display}"


def _process_single_row(
    row: SupplierRow,
    context: ProcessingContext,
) -> RowProcessResult:
    """Process a single supplier row and return aggregated counts."""
    supplier_code = row["supplier_code"]
    entry = context.entries_by_code.get(supplier_code)
    if entry is not None and entry["ignore"]:
        comment = entry["comment"] or f"{row['description']} | {row['size']}"
        line = f"[IGNORED] Supplier {row['supplier_code']} | {comment}"
        line = _append_airtable_record_url(line, supplier_code, context.airtable_record_urls_by_code)
        context.report.ignored_rows.append((row, comment))
        _emit(context, line, style="cyan")
        return RowProcessResult(False, 0, 0, 1, 0, 0, 0, 0, 0, 0)

    mapping = context.mappings_by_code.get(supplier_code)
    if mapping is None:
        line = f"[UNMAPPED] Supplier {row['supplier_code']} | {row['description']} | {row['size']}"
        line = _append_airtable_record_url(line, supplier_code, context.airtable_record_urls_by_code)
        context.report.unmapped_rows.append(row)
        _emit(context, line, style="yellow")
        return RowProcessResult(False, 0, 1, 0, 0, 0, 0, 0, 0, 0)

    if row["pack"] <= 0:
        line = f"Supplier code {supplier_code} has invalid pack size {row['pack']}"
        _emit(context, line, style="yellow")
        return RowProcessResult(False, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    plu = mapping["plu"]
    till_product = context.till_products.get(plu)
    if till_product is None:
        line = f"PLU {plu} mapped from supplier code {supplier_code} not found on till"
        context.report.missing_plu_rows.append((supplier_code, plu))
        _emit(context, line, style="yellow")
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

    update_status, update_error = _apply_single_cost_update(request)
    if update_status == "failed" and update_error:
        failed_line = f"[FAILED] Supplier {row['supplier_code']} | PLU {plu} {till_product['name']} | {update_error}"
        failed_line = _append_airtable_record_url(
            failed_line,
            supplier_code,
            context.airtable_record_urls_by_code,
        )
        context.report.failed_update_messages.append(failed_line)
        context.report.errors.append(failed_line)
        _emit(context, failed_line, style="yellow")

    if update_status == "updated" and item_snapshot is not None:
        _apply_successful_variant_update_to_snapshot(
            item_snapshot,
            till_product["variant_id"],
            change_context.cost_per_serving_pounds,
            new_barcode,
        )

    cost_line = _format_cost_line(
        row,
        plu,
        till_product["name"],
        change_context.cost_per_serving_pounds,
        change_context,
    )
    cost_line = _append_airtable_record_url(cost_line, supplier_code, context.airtable_record_urls_by_code)
    style = "green" if (change_context.cost_changed or change_context.ean_changed) else None
    _emit(context, cost_line, style=style)

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


def _empty_summary() -> SupplierUpdateSummary:
    return SupplierUpdateSummary(
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
    )


def _sync_airtable_mapping(
    rows: list[SupplierRow],
    mapping_store: AirtableSupplierMappingStore,
    report: SupplierUpdateReport,
    delivery_date: date,
) -> None:
    updated_labels = mapping_store.update_labels(rows)
    if updated_labels > 0:
        report.lines.append(
            f"Updated {updated_labels} supplier product label{'s' if updated_labels != 1 else ''} in Airtable",
        )

    mapping_store.queue_last_supplier_updates(rows, updated_at=delivery_date)
    newly_seeded_rows = mapping_store.seed_missing_codes(
        rows,
        last_supplier_update=delivery_date,
    )
    if newly_seeded_rows:
        report.newly_seeded_rows.extend(newly_seeded_rows)
        report.lines.append(
            "Added "
            f"{len(newly_seeded_rows)} new supplier product entr"
            f"{'y' if len(newly_seeded_rows) == 1 else 'ies'} to Airtable",
        )

    mapping_store.flush_updates()


def run_supplier_cost_updates(
    confirmation_text: str,
    mapping_store: AirtableSupplierMappingStore,
    apply: bool,
    *,
    write_mapping: bool = True,
) -> SupplierUpdateReport:
    report = SupplierUpdateReport(summary=_empty_summary())

    rows = parse_supplier_confirmation_rows(confirmation_text)
    if not rows:
        msg = "No order rows were parsed from supplier confirmation."
        report.errors.append(msg)
        raise ValueError(msg)

    delivery_date = parse_supplier_confirmation_delivery_date(confirmation_text)

    if write_mapping:
        _sync_airtable_mapping(rows, mapping_store, report, delivery_date)

    entries_by_code = mapping_store.get_entries()
    mappings_by_code = get_active_mapping_by_code(entries_by_code)
    report.airtable_record_urls_by_code = mapping_store.get_record_urls_by_code()
    report.airtable_table_url = mapping_store.get_table_url()

    items = get_loyverse_items()
    till_products = build_till_products(items)
    items_by_id = {str(item.get("id", "")): item for item in items}

    processing_ctx = ProcessingContext(
        entries_by_code=entries_by_code,
        mappings_by_code=mappings_by_code,
        till_products=till_products,
        items_by_id=items_by_id,
        apply=apply,
        report=report,
        airtable_record_urls_by_code=report.airtable_record_urls_by_code,
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
    cost_changed_supplier_codes: set[int] = set()

    for row in rows:
        result = _process_single_row(row, processing_ctx)
        if result.processed:
            mapped_rows += result.mapped_count
            changed_costs += result.cost_changed_count
            changed_eans += result.ean_changed_count
            if result.cost_changed_count > 0:
                cost_changed_supplier_codes.add(row["supplier_code"])
            updated += result.updated_count
            failed += result.failed_count
            skipped += result.skipped_count
        else:
            missing_codes += result.missing_code_count
            ignored_rows += result.ignored_count
            missing_plus += result.missing_plu_count

    if write_mapping and cost_changed_supplier_codes:
        mapping_store.queue_last_cost_changes(
            cost_changed_supplier_codes,
            changed_at=delivery_date,
        )
        mapping_store.flush_updates()

    report.summary = SupplierUpdateSummary(
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
    return report


def _format_report_summary_lines(summary: SupplierUpdateSummary) -> list[str]:
    return [
        "Supplier cost update completed.",
        "",
        f"Parsed supplier rows: {summary.parsed_rows}",
        f"Mapped output rows: {summary.mapped_rows}",
        f"Missing supplier codes: {summary.missing_supplier_codes}",
        f"Ignored supplier rows: {summary.ignored_supplier_rows}",
        f"Missing PLUs on till: {summary.missing_plus_on_till}",
        f"Rows with changed cost: {summary.rows_with_changed_cost}",
        f"Rows with changed EAN: {summary.rows_with_changed_ean}",
    ]


def _format_supplier_row_line(row: SupplierRow, *, record_urls_by_code: dict[int, str]) -> str:
    line = f"{row['supplier_code']} | {row['description']} | {row['size']}"
    url = record_urls_by_code.get(row["supplier_code"])
    if url is None:
        return f"  {line}"
    return f"  {line} — {url}"


def _existing_unmapped_rows(report: SupplierUpdateReport) -> list[SupplierRow]:
    seeded_codes = {seeded_row["supplier_code"] for seeded_row in report.newly_seeded_rows}
    return [row for row in report.unmapped_rows if row["supplier_code"] not in seeded_codes]


def _format_airtable_mapping_footer(report: SupplierUpdateReport) -> list[str]:
    if not (report.newly_seeded_rows or _existing_unmapped_rows(report)):
        return []
    if report.airtable_table_url is None:
        return []

    return [
        "",
        "Please update new supplier codes in Airtable with PLU and servings data:",
        report.airtable_table_url,
    ]


def _format_report_detail_sections(report: SupplierUpdateReport) -> list[str]:
    sections: list[str] = []
    record_urls_by_code = report.airtable_record_urls_by_code

    if report.newly_seeded_rows:
        sections.extend(
            [
                "",
                "New supplier products seeded in Airtable (add PLU mapping):",
                *[
                    _format_supplier_row_line(row, record_urls_by_code=record_urls_by_code)
                    for row in report.newly_seeded_rows
                ],
            ],
        )

    existing_unmapped_rows = _existing_unmapped_rows(report)
    if existing_unmapped_rows:
        sections.extend(
            [
                "",
                "Unmapped supplier codes (add PLU mapping in Airtable):",
                *[
                    _format_supplier_row_line(row, record_urls_by_code=record_urls_by_code)
                    for row in existing_unmapped_rows
                ],
            ],
        )

    if report.ignored_rows:
        sections.extend(
            [
                "",
                "Ignored supplier rows:",
                *[f"  {row['supplier_code']} | {comment}" for row, comment in report.ignored_rows],
            ],
        )

    if report.missing_plu_rows:
        sections.extend(
            [
                "",
                "Missing PLUs on till:",
                *[
                    f"  Supplier {supplier_code} maps to missing PLU {plu}"
                    for supplier_code, plu in report.missing_plu_rows
                ],
            ],
        )

    if report.failed_update_messages:
        sections.extend(
            [
                "",
                "Failed API updates:",
                *[f"  {message}" for message in report.failed_update_messages],
            ],
        )

    return sections


def format_supplier_update_report(report: SupplierUpdateReport, *, include_details: bool = True) -> str:
    sections: list[str] = []

    if report.errors:
        sections.append("Errors:")
        sections.extend(f"  {error}" for error in report.errors)
        sections.append("")

    sections.extend(_format_report_summary_lines(report.summary))
    sections.extend(_format_report_detail_sections(report))

    if include_details and report.lines:
        sections.extend(["", "Details:", *report.lines])

    sections.extend(_format_airtable_mapping_footer(report))

    return "\n".join(sections)
