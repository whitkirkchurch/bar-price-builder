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
from rendering import build_price_list_pdfs
from supplier_updates import print_supplier_update_summary, run_supplier_cost_updates


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
    try:
        summary = run_supplier_cost_updates(
            supplier_confirmation_file=supplier_confirmation_file,
            mapping_file=mapping_file,
            apply=apply,
        )
    except ValueError as exc:
        click.echo(click.style(f"Error: {exc}", fg="red"), err=True)
        return
    except requests.RequestException as exc:
        click.echo(click.style(f"API Error: {exc}", fg="red"), err=True)
        return

    print_supplier_update_summary(summary, apply)


if __name__ == "__main__":
    cli()
