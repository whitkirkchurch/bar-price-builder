import click

from loyverse import TillProduct
from price_list import PluPrice, get_all_ignored_plus, get_all_plus_in_price_list


def validate_till_prices(till_products: dict[int, TillProduct], plus_in_price_list: dict[int, PluPrice]) -> None:
    ignored_plus = set(get_all_ignored_plus())

    for product in till_products.values():
        if not product["available_for_sale"]:
            continue

        if product["plu"] in ignored_plus:
            continue

        if not product["till_price_set"]:
            click.echo(
                click.style(
                    f"NO TILL PRICE: PLU {product['plu']} ({product['name']}) has no default price set in Loyverse",
                    fg="yellow",
                ),
            )
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
