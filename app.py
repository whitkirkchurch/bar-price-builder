from pathlib import Path

import click
import requests

from config import DATA_DIR
from loyverse import fetch_till_products
from price_check import (
    validate_till_prices,
    warn_for_configured_plus_not_on_till,
    warn_for_ignored_plus_not_on_till,
)
from price_list import get_all_plus_in_price_list
from product_images import print_product_image_summary, run_product_image_sync
from rendering import build_price_list_pdfs
from supplier_email import extract_supplier_confirmation_text, parse_raw_email
from supplier_updates import SupplierUpdateReport, run_supplier_cost_updates


def _print_supplier_update_summary(report: SupplierUpdateReport, apply: bool) -> None:
    summary = report.summary
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


@click.group()
def cli():
    pass


@cli.command()
def check():
    click.echo("Sense checking price list…")

    try:
        till_products = fetch_till_products()
        click.echo(f"Fetched {len(till_products)} items from Loyverse API")
    except ValueError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        return
    except requests.RequestException as exc:
        click.echo(click.style(f"API Error: {exc}", fg="red"), err=True)
        return

    validate_till_prices(till_products, get_all_plus_in_price_list())
    warn_for_ignored_plus_not_on_till(till_products)
    warn_for_configured_plus_not_on_till(till_products)

    click.echo(click.style("Done!", fg="green"))


@cli.command()
def build():
    click.echo("Building outputs…")
    build_price_list_pdfs()
    click.echo(click.style("Done!", fg="green"))


@cli.command("update-costs-from-supplier")
@click.argument("supplier_confirmation_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--mapping-file",
    type=click.Path(exists=True, path_type=Path),
    default=DATA_DIR / "supplier_data.yaml",
    show_default=True,
    help="YAML file mapping each supplier code to a single PLU and servings_per_unit.",
)
@click.option(
    "--apply",
    is_flag=True,
    help="Apply updates to Loyverse API. Without this flag, command runs as dry-run.",
)
def update_costs_from_supplier(
    supplier_confirmation_file: Path,
    mapping_file: Path,
    apply: bool,
) -> None:
    click.echo("Reading supplier confirmation…")
    try:
        report = run_supplier_cost_updates(
            confirmation_text=supplier_confirmation_file.read_text(),
            mapping_file=mapping_file,
            apply=apply,
        )
    except ValueError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        return
    except requests.RequestException as exc:
        click.echo(click.style(f"API Error: {exc}", fg="red"), err=True)
        return

    _print_supplier_update_summary(report, apply)


@cli.command("parse-supplier-email")
@click.argument("email_file", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--mapping-file",
    type=click.Path(exists=True, path_type=Path),
    default=DATA_DIR / "supplier_data.yaml",
    show_default=True,
    help="YAML file mapping each supplier code to a single PLU and servings_per_unit.",
)
@click.option(
    "--apply",
    is_flag=True,
    help="Apply updates to Loyverse API. Without this flag, command runs as dry-run.",
)
@click.option(
    "--extract-only",
    is_flag=True,
    help="Only extract and print confirmation text; do not run cost updates.",
)
def parse_supplier_email(
    email_file: Path,
    mapping_file: Path,
    apply: bool,
    extract_only: bool,
) -> None:
    try:
        message = parse_raw_email(email_file.read_bytes())
        confirmation_text = extract_supplier_confirmation_text(message)
    except ValueError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        return

    if extract_only:
        click.echo(confirmation_text)
        return

    click.echo("Reading supplier confirmation…")
    try:
        report = run_supplier_cost_updates(
            confirmation_text=confirmation_text,
            mapping_file=mapping_file,
            apply=apply,
        )
    except ValueError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        return
    except requests.RequestException as exc:
        click.echo(click.style(f"API Error: {exc}", fg="red"), err=True)
        return

    _print_supplier_update_summary(report, apply)


@cli.command("build-product-images")
@click.option(
    "--products-file",
    type=click.Path(exists=True, path_type=Path),
    default=DATA_DIR / "products.yaml",
    show_default=True,
    help="YAML file defining product image defaults and per-product ID overrides.",
)
@click.option(
    "--write",
    is_flag=True,
    help="Upload generated images to Loyverse. Without this flag, command only builds local images.",
)
def build_product_images(products_file: Path, write: bool) -> None:
    click.echo("Building product images…")
    try:
        summary = run_product_image_sync(products_file=products_file, write=write)
    except ValueError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        return
    except requests.RequestException as exc:
        click.echo(click.style(f"API Error: {exc}", fg="red"), err=True)
        return

    print_product_image_summary(summary, write)
    click.echo(click.style("Done!", fg="green"))


if __name__ == "__main__":
    cli()
