from os import getenv
from typing import TypedDict

import requests

LOYVERSE_API_BASE_URL = "https://api.loyverse.com/v1.0"


class TillProduct(TypedDict):
    plu: int
    name: str
    price: int
    till_price_set: bool
    available_for_sale: bool
    item_id: str
    variant_id: str
    cost_pence: float | None
    barcode: str | None


def _build_variant_display_name(item_name: str, variant: dict) -> str:
    option_values = []
    for key in ("option1_value", "option2_value", "option3_value"):
        raw_value = variant.get(key)
        if raw_value is None:
            option_values.append("")
            continue
        option_values.append(str(raw_value).strip())
    option_values = [value for value in option_values if value]
    if not option_values:
        return item_name

    joined_options = " / ".join(option_values)
    return f"{item_name} ({joined_options})"


def get_loyverse_headers() -> dict[str, str]:
    loyverse_pat = getenv("LOYVERSE_PAT")
    if not loyverse_pat:
        msg = "LOYVERSE_PAT environment variable is not set"
        raise ValueError(msg)

    return {
        "Authorization": f"Bearer {loyverse_pat}",
        "Content-Type": "application/json",
    }


def get_loyverse_auth_headers() -> dict[str, str]:
    loyverse_pat = getenv("LOYVERSE_PAT")
    if not loyverse_pat:
        msg = "LOYVERSE_PAT environment variable is not set"
        raise ValueError(msg)
    return {"Authorization": f"Bearer {loyverse_pat}"}


def get_loyverse_items() -> list[dict]:
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

        cursor = data.get("cursor")
        if not cursor:
            break

    return items


def extract_variant_cost_pence(variant: dict) -> float | None:
    for key in ("cost", "supply_price", "default_cost", "purchase_price"):
        if key not in variant or variant[key] is None:
            continue

        try:
            return round(float(variant[key]) * 100, 4)
        except (TypeError, ValueError):
            continue

    return None


def build_till_products(items: list[dict]) -> dict[int, TillProduct]:
    till_products: dict[int, TillProduct] = {}

    for item in items:
        item_id = str(item.get("id", ""))
        item_name = item.get("item_name", "")

        for variant in item.get("variants", []):
            plu = variant.get("sku", "")
            if not plu or not plu.isnumeric():
                continue

            variant_id = str(variant.get("variant_id", variant.get("id", "")))
            variant_item_id = str(variant.get("item_id", ""))
            resolved_item_id = variant_item_id or item_id

            default_price_raw = variant.get("default_price")
            till_price_set = default_price_raw is not None
            default_price = 0 if default_price_raw is None else default_price_raw
            stores = variant.get("stores", [])
            available_for_sale = True if not stores else any(store.get("available_for_sale", False) for store in stores)
            raw_barcode = variant.get("barcode")
            barcode = None if raw_barcode is None else str(raw_barcode).strip() or None

            till_products[int(plu)] = {
                "plu": int(plu),
                "name": _build_variant_display_name(item_name, variant),
                "price": round(float(default_price) * 100),
                "till_price_set": till_price_set,
                "available_for_sale": available_for_sale,
                "item_id": resolved_item_id,
                "variant_id": variant_id,
                "cost_pence": extract_variant_cost_pence(variant),
                "barcode": barcode,
            }

    return till_products


def fetch_till_products() -> dict[int, TillProduct]:
    return build_till_products(get_loyverse_items())


def _build_variant_payload(
    variant: dict,
    target_variant_id: str,
    expected_sku: str,
    cost_pounds: float,
    barcode: str | None,
) -> tuple[dict, bool, str]:
    variant_payload: dict = {"variant_id": variant.get("variant_id", variant.get("id"))}
    for key in (
        "item_id",
        "sku",
        "reference_variant_id",
        "option1_value",
        "option2_value",
        "option3_value",
        "barcode",
        "purchase_cost",
        "default_pricing_type",
        "default_price",
        "stores",
    ):
        if key in variant:
            variant_payload[key] = variant[key]

    current_variant_id = str(variant.get("variant_id", variant.get("id", "")))
    if current_variant_id != target_variant_id:
        if "cost" in variant:
            variant_payload["cost"] = variant["cost"]
        return variant_payload, False, ""

    current_sku = str(variant.get("sku", "")).strip()
    if current_sku != expected_sku:
        return (
            variant_payload,
            False,
            f"Variant/SKU mismatch before update (expected SKU {expected_sku}, got {current_sku}).",
        )

    variant_payload["cost"] = cost_pounds
    if barcode is not None:
        variant_payload["barcode"] = barcode
    return variant_payload, True, ""


def build_item_upsert_payload(
    item_snapshot: dict,
    variant_id: str,
    expected_plu: int,
    cost_pounds: float,
    barcode: str | None = None,
) -> tuple[dict | None, str]:
    item_id = str(item_snapshot.get("id", ""))
    item_name = item_snapshot.get("item_name", "")
    if not item_id or not item_name:
        return None, "Item snapshot missing id or item_name"

    payload: dict = {
        "id": item_id,
        "item_name": item_name,
    }

    for key in (
        "description",
        "reference_id",
        "category_id",
        "track_stock",
        "sold_by_weight",
        "is_composite",
        "use_production",
        "components",
        "primary_supplier_id",
        "tax_ids",
        "modifiers_ids",
        "form",
        "color",
        "option1_name",
        "option2_name",
        "option3_name",
    ):
        if key in item_snapshot:
            payload[key] = item_snapshot[key]

    payload_variants: list[dict] = []
    found_variant = False
    expected_sku = str(expected_plu)

    for variant in item_snapshot.get("variants", []):
        variant_payload, is_target, error = _build_variant_payload(
            variant,
            variant_id,
            expected_sku,
            cost_pounds,
            barcode,
        )
        if error:
            return None, error
        found_variant = found_variant or is_target
        payload_variants.append(variant_payload)

    if not found_variant:
        return None, f"Variant {variant_id} not found in item snapshot"

    payload["variants"] = payload_variants
    return payload, ""


def _request_item_upsert(payload: dict) -> tuple[requests.Response | None, str]:
    endpoint = f"{LOYVERSE_API_BASE_URL}/items"
    method = "POST"

    try:
        response = requests.request(
            method,
            endpoint,
            headers=get_loyverse_headers(),
            json=payload,
            timeout=10,
        )
    except requests.RequestException as exc:
        return None, f"{method} {endpoint} -> exception {exc}"

    return response, ""


def _find_variant_by_id(variants: list[dict], variant_id: str) -> dict | None:
    for variant in variants:
        if str(variant.get("variant_id", variant.get("id", ""))) == variant_id:
            return variant
    return None


def _validate_upsert_response(  # noqa: PLR0911
    response: requests.Response,
    item_snapshot: dict,
    variant_id: str,
    expected_plu: int,
) -> tuple[bool, str]:
    endpoint = f"POST {LOYVERSE_API_BASE_URL}/items"
    body_preview = response.text.strip().replace("\n", " ")[:300]
    if not response.ok:
        return False, f"{endpoint} -> {response.status_code} {body_preview}"

    try:
        response_data = response.json()
    except ValueError:
        return False, f"{endpoint} -> 200 with non-JSON body: {body_preview}"

    item_id = str(item_snapshot.get("id", ""))
    response_item_id = str(response_data.get("id", ""))
    if response_item_id != item_id:
        return (
            False,
            f"{endpoint} -> id mismatch (expected {item_id}, got {response_item_id}). Refusing potential insert.",
        )

    original_category_id = item_snapshot.get("category_id")
    if response_data.get("category_id") != original_category_id:
        return (
            False,
            f"{endpoint} -> category changed ({original_category_id} -> {response_data.get('category_id')}). Refusing mutation.",
        )

    matched_variant = _find_variant_by_id(response_data.get("variants", []), variant_id)
    if matched_variant is None:
        return False, f"{endpoint} -> target variant {variant_id} missing in response"

    expected_sku = str(expected_plu)
    response_sku = str(matched_variant.get("sku", "")).strip()
    if response_sku != expected_sku:
        return False, f"{endpoint} -> sku changed ({expected_sku} -> {response_sku}). Refusing potential PLU change."

    return True, "updated"


def update_loyverse_cost(
    item_snapshot: dict,
    variant_id: str,
    expected_plu: int,
    cost_pounds: float,
    barcode: str | None = None,
) -> tuple[bool, str]:
    payload, payload_error = build_item_upsert_payload(
        item_snapshot,
        variant_id,
        expected_plu,
        cost_pounds,
        barcode,
    )
    if payload is None:
        return False, payload_error

    response, request_error = _request_item_upsert(payload)
    if response is None:
        return False, request_error

    return _validate_upsert_response(response, item_snapshot, variant_id, expected_plu)


def upload_item_image(item_id: str, image_bytes: bytes) -> tuple[bool, str]:
    endpoint = f"{LOYVERSE_API_BASE_URL}/items/{item_id}/image"
    headers = {
        **get_loyverse_auth_headers(),
        "Content-Type": "image/png",
    }

    try:
        response = requests.post(endpoint, headers=headers, data=image_bytes, timeout=30)
    except requests.RequestException as exc:
        return False, f"POST {endpoint} -> exception {exc}"

    if response.ok:
        return True, "uploaded"

    body_preview = response.text.strip().replace("\n", " ")[:300]
    return False, f"POST {endpoint} -> {response.status_code} {body_preview}"
