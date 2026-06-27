from functools import cache
from typing import TypedDict

from ruamel.yaml import YAML

from config import DATA_DIR

YAML_SAFE = YAML(typ="safe")


class PluPrice(TypedDict):
    price: int


def format_currency(value) -> str:
    return f"£{value / 100:,.2f}"


def is_list(value) -> bool:
    return isinstance(value, list)


@cache
def get_all_ignored_plus() -> list[int]:
    with (DATA_DIR / "ignore.yaml").open() as ignore_file:
        return YAML_SAFE.load(ignore_file)


@cache
def get_all_plus_in_price_list() -> dict[int, PluPrice]:
    with (DATA_DIR / "prices.yaml").open() as prices_file:
        price_data = YAML_SAFE.load(prices_file)

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


def plu_report_row_discardable(row) -> bool:
    if row["plu"] == "":
        return True

    if not row["plu"].isnumeric():
        return True

    if int(row["plu"]) in get_all_ignored_plus():
        return True

    if row["name"].strip() == "":
        return True

    if row["name"].startswith("PLU"):
        return True

    return bool(row["name"].startswith("XXX"))
