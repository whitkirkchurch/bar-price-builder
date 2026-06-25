from datetime import datetime
from functools import cache
from os import getenv
from pathlib import Path
from typing import TypedDict

import click
import pytz
import requests
import yaml
from jinja2 import Environment, PackageLoader, select_autoescape
from weasyprint import CSS, HTML
from weasyprint.text.fonts import FontConfiguration

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "outputs"

# Loyverse API configuration
LOYVERSE_API_BASE_URL = "https://api.loyverse.com/v1.0"
LOYVERSE_PAT = getenv("LOYVERSE_PAT")


def get_loyverse_headers() -> dict[str, str]:
    """Get headers for Loyverse API requests."""
    if not LOYVERSE_PAT:
        msg = "LOYVERSE_PAT environment variable is not set"
        raise ValueError(msg)
    return {
        "Authorization": f"Bearer {LOYVERSE_PAT}",
        "Content-Type": "application/json",
    }


def get_loyverse_items() -> list[dict]:
    """
    Fetch all items from Loyverse API with pagination.
    Returns list of items with PLU, name, and price data.
    """
    items = []
    cursor = None
    headers = get_loyverse_headers()

    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor

        response = requests.get(
            f"{LOYVERSE_API_BASE_URL}/items",
            headers=headers,
            params=params,
            timeout=10,
        )
        response.raise_for_status()

        data = response.json()
        items.extend(data.get("items", []))

        # Check if there are more pages
        cursor = data.get("cursor")
        if not cursor:
            break

    return items


class PluPrice(TypedDict):
    price: int


class TillProduct(TypedDict):
    plu: int
    name: str
    price: int
    available_for_sale: bool


def format_currency(value) -> str:
    return f"£{value / 100:,.2f}"


def is_list(value) -> bool:
    return isinstance(value, list)


def plu_report_row_discardable(row) -> bool:  ## noqa: PLR0911
    # Skip if PLU is empty
    if row["plu"] == "":
        return True

    # Skip if PLU is not numeric
    if not row["plu"].isnumeric():
        return True

    # Skip if PLU is in ignore file
    if int(row["plu"]) in get_all_ignored_plus():
        return True

    # Skip if name is empty
    if row["name"].strip() == "":
        return True

    # Skip if name begins with 'PLU'
    if row["name"].startswith("PLU"):
        return True

    # Skip if name begins with 'XXX'
    if row["name"].startswith("XXX"):  ## noqa: SIM103
        return True

    # If we've reached here, it can't be discarded.
    return False


def write_html_to_pdf_with_styles(html: str, output_filename: str) -> None:
    font_config = FontConfiguration()

    with (Path(__file__).parent / "styles" / "styles.css").open() as css_file:
        css = CSS(string=css_file.read(), font_config=font_config)

    pdf = HTML(string=html)

    pdf.write_pdf(
        OUTPUT_DIR / output_filename,
        stylesheets=[css],
        font_config=font_config,
    )


@cache
def get_all_ignored_plus() -> list[int]:
    with (DATA_DIR / "ignore.yaml").open() as ignore_file:
        return yaml.safe_load(ignore_file)


@cache
def get_all_plus_in_price_list() -> dict[int, PluPrice]:
    with (DATA_DIR / "prices.yaml").open() as prices_file:
        price_data = yaml.safe_load(prices_file)

    plus: dict[int, PluPrice] = {}

    for category in price_data:
        for item in category["items"]:
            if isinstance(item, list):
                for variant in item:
                    if "plus" in variant:
                        for plu in variant["plus"]:
                            plus[int(plu)] = {"price": variant["price"]}
            elif "plus" in item:
                for plu in item["plus"]:
                    plus[plu] = {"price": item["price"]}

    return plus


def build_till_products(items: list[dict]) -> dict[int, TillProduct]:
    till_products: dict[int, TillProduct] = {}

    for item in items:
        name = item.get("item_name", "")

        variants = item.get("variants", [])
        for variant in variants:
            plu = variant.get("sku", "")
            if not plu or not plu.isnumeric():
                continue

            default_price = variant.get("default_price", 0)
            stores = variant.get("stores", [])
            available_for_sale = True if not stores else any(store.get("available_for_sale", False) for store in stores)

            till_products[int(plu)] = {
                "plu": int(plu),
                "name": name,
                "price": round(float(default_price) * 100),
                "available_for_sale": available_for_sale,
            }

    return till_products


def validate_till_prices(till_products: dict[int, TillProduct], plus_in_price_list: dict[int, PluPrice]) -> None:
    ignored_plus = set(get_all_ignored_plus())

    for product in till_products.values():
        if not product["available_for_sale"]:
            continue

        if product["plu"] in ignored_plus:
            continue

        if product["plu"] not in plus_in_price_list:
            click.echo(
                click.style(
                    f"MISSING: PLU {product['plu']} ({product['name']}) is missing from price list, till price is {product['price']}",
                    fg="red",
                ),
            )
            continue

        product_plu_price = plus_in_price_list[product["plu"]]

        if product_plu_price["price"] != product["price"]:
            click.echo(
                click.style(
                    f"PRICE MISMATCH: PLU {product['plu']} ({product['name']}) is priced {product['price']} on the till but {product_plu_price['price']} on the list",
                    fg="red",
                ),
            )


def warn_for_ignored_plus_not_on_till(till_products: dict[int, TillProduct]) -> None:
    till_plus = set(till_products.keys())
    for plu in sorted(get_all_ignored_plus()):
        if plu not in till_plus:
            click.echo(
                click.style(
                    f"NOT REAL PLU: PLU {plu} is in ignore.yaml but doesn't exist on the till",
                    fg="yellow",
                ),
            )
        elif not till_products[plu]["available_for_sale"]:
            click.echo(
                click.style(
                    f"IGNORED + NOT FOR SALE: PLU {plu} ({till_products[plu]['name']}) is in ignore.yaml and not currently for sale in Loyverse",
                    fg="yellow",
                ),
            )


def warn_for_configured_plus_not_on_till(till_products: dict[int, TillProduct]) -> None:
    till_plus = set(till_products.keys())
    for plu in sorted(get_all_plus_in_price_list().keys()):
        if plu not in till_plus:
            click.echo(
                click.style(
                    f"NOT REAL PLU: PLU {plu} is in prices.yaml but doesn't exist on the till",
                    fg="yellow",
                ),
            )


@click.group()
def cli():
    pass


@cli.command()
def check():
    click.echo("Sense checking price list…")

    # Fetch from Loyverse API
    try:
        items = get_loyverse_items()
        till_products = build_till_products(items)
        click.echo(f"Fetched {len(till_products)} items from Loyverse API")
    except ValueError as e:
        click.echo(click.style(f"Error: {e}", fg="red"), err=True)
        return
    except requests.RequestException as e:
        click.echo(click.style(f"API Error: {e}", fg="red"), err=True)
        return

    validate_till_prices(till_products, get_all_plus_in_price_list())
    warn_for_ignored_plus_not_on_till(till_products)
    warn_for_configured_plus_not_on_till(till_products)

    click.echo(click.style("Done!", fg="green"))


@cli.command()
def build():
    click.echo("Building outputs…")

    with (DATA_DIR / "prices.yaml").open() as prices_file:
        data = {
            "generated_time": datetime.now(tz=pytz.timezone("Europe/London")).strftime("%Y-%m-%d %H:%M"),
            "prices_data": yaml.safe_load(prices_file),
        }

    env = Environment(loader=PackageLoader("app"), autoescape=select_autoescape())

    env.filters["format_currency"] = format_currency
    env.filters["is_list"] = is_list

    a3_template = env.get_template("A3.jinja")
    a3_html = a3_template.render(**data)

    write_html_to_pdf_with_styles(a3_html, "A3.pdf")

    a5_template = env.get_template("A5.jinja")
    a5_html = a5_template.render(**data)

    write_html_to_pdf_with_styles(a5_html, "A5.pdf")

    click.echo(click.style("Done!", fg="green"))


if __name__ == "__main__":
    cli()
