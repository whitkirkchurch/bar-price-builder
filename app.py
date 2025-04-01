import csv
from datetime import datetime
from functools import cache
from pathlib import Path
from typing import TypedDict

import click
import pytz
import yaml
from jinja2 import Environment, PackageLoader, select_autoescape
from weasyprint import CSS, HTML
from weasyprint.text.fonts import FontConfiguration

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_DIR = Path(__file__).parent / "outputs"


class PluPrice(TypedDict):
    price: int


def format_currency(value) -> str:
    return f"£{value / 100:,.2f}"


def is_list(value) -> bool:
    return isinstance(value, list)


def plu_report_row_discardable(row) -> bool:
    # Skip if PLU is empty
    if row["plu"] == "":
        return True

    # Skip if PLU is not numeric
    if not row["plu"].isnumeric():
        return True

    # Skip if PLU is in ignore file
    if int(row["plu"]) in get_all_ignored_plus():
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


@click.group()
def cli():
    pass


@cli.command()
@click.argument("file", type=click.File("r"))
def check(file):
    click.echo("Sense checking price list…")

    products: list[dict] = []

    for row in csv.DictReader(file, fieldnames=("plu", "code", "name", "price")):
        if plu_report_row_discardable(row):
            continue

        products.append(row)

    plus_in_price_list = get_all_plus_in_price_list()

    for product in products:
        product_price = round(float(product["price"]) * 100)

        if int(product["plu"]) not in plus_in_price_list:
            click.echo(
                click.style(
                    f"MISSING: PLU {product['plu']} ({product['name']}) is missing from price list, till price is {product_price}",
                    fg="red",
                ),
            )
            continue

        product_plu_price = plus_in_price_list[int(product["plu"])]

        if product_plu_price["price"] != product_price:
            click.echo(
                click.style(
                    f"PRICE MISMATCH: PLU {product['plu']} ({product['name']}) is priced {product_price} on the till but {product_plu_price['price']} on the list",
                    fg="red",
                ),
            )

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
