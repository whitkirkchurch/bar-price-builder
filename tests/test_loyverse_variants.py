from loyverse import build_item_upsert_payload, build_till_products


def test_build_till_products_includes_variant_option_values_in_name() -> None:
    items = [
        {
            "id": "item-1",
            "item_name": "Test Snacks",
            "variants": [
                {
                    "variant_id": "v-original",
                    "sku": "10005",
                    "default_price": 2.5,
                    "cost": 0.75,
                    "option1_value": "Original",
                    "barcode": "6000000000001",
                },
                {
                    "variant_id": "v-paprika",
                    "sku": "10006",
                    "default_price": 2.5,
                    "cost": 0.75,
                    "option1_value": "Paprika",
                    "barcode": "6000000000002",
                },
            ],
        },
    ]

    till_products = build_till_products(items)

    assert till_products[10005]["name"] == "Test Snacks (Original)"
    assert till_products[10006]["name"] == "Test Snacks (Paprika)"
    assert till_products[10005]["variant_id"] == "v-original"
    assert till_products[10006]["variant_id"] == "v-paprika"


def test_build_till_products_ignores_none_option_values_in_name() -> None:
    items = [
        {
            "id": "item-2",
            "item_name": "Test Snacks",
            "variants": [
                {
                    "variant_id": "v-sour-cream",
                    "sku": "10008",
                    "default_price": 2.5,
                    "cost": 0.75,
                    "option1_value": "Sour Cream & Onion",
                    "option2_value": None,
                    "option3_value": None,
                },
            ],
        },
    ]

    till_products = build_till_products(items)

    assert till_products[10008]["name"] == "Test Snacks (Sour Cream & Onion)"


def test_build_item_upsert_payload_updates_only_target_variant_for_multi_variant_item() -> None:
    item_snapshot = {
        "id": "item-1",
        "item_name": "Test Snacks",
        "variants": [
            {
                "variant_id": "v-original",
                "sku": "10005",
                "cost": 0.75,
                "barcode": "6000000000001",
                "default_price": 2.5,
            },
            {
                "variant_id": "v-paprika",
                "sku": "10006",
                "cost": 0.70,
                "barcode": "6000000000002",
                "default_price": 2.5,
            },
        ],
    }

    payload, error = build_item_upsert_payload(
        item_snapshot=item_snapshot,
        variant_id="v-original",
        expected_plu=10005,
        cost_pounds=0.81,
        barcode="0123456789012",
    )

    assert error == ""
    assert payload is not None

    by_variant_id = {variant["variant_id"]: variant for variant in payload["variants"]}

    assert by_variant_id["v-original"]["cost"] == 0.81
    assert by_variant_id["v-original"]["barcode"] == "0123456789012"

    # Non-target variant must keep its original data.
    assert by_variant_id["v-paprika"]["cost"] == 0.70
    assert by_variant_id["v-paprika"]["barcode"] == "6000000000002"


def test_build_item_upsert_payload_rejects_snapshot_missing_item_name() -> None:
    item_snapshot = {
        "id": "item-1",
        "variants": [
            {
                "variant_id": "v-original",
                "sku": "10005",
                "cost": 0.75,
            },
        ],
    }

    payload, error = build_item_upsert_payload(
        item_snapshot=item_snapshot,
        variant_id="v-original",
        expected_plu=10005,
        cost_pounds=0.81,
    )

    assert payload is None
    assert "missing id or item_name" in error


def test_build_item_upsert_payload_rejects_variant_sku_mismatch() -> None:
    item_snapshot = {
        "id": "item-1",
        "item_name": "Test Snacks",
        "variants": [
            {
                "variant_id": "v-original",
                "sku": "99999",
                "cost": 0.75,
                "default_price": 2.5,
            },
        ],
    }

    payload, error = build_item_upsert_payload(
        item_snapshot=item_snapshot,
        variant_id="v-original",
        expected_plu=10005,
        cost_pounds=0.81,
    )

    assert payload is None
    assert "Variant/SKU mismatch before update" in error


def test_build_item_upsert_payload_rejects_missing_variant_id() -> None:
    item_snapshot = {
        "id": "item-1",
        "item_name": "Test Snacks",
        "variants": [
            {
                "variant_id": "v-original",
                "sku": "10005",
                "cost": 0.75,
            },
        ],
    }

    payload, error = build_item_upsert_payload(
        item_snapshot=item_snapshot,
        variant_id="v-missing",
        expected_plu=10005,
        cost_pounds=0.81,
    )

    assert payload is None
    assert "Variant v-missing not found" in error
