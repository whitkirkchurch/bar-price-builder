from supplier_updates import _apply_successful_variant_update_to_snapshot


def test_apply_successful_variant_update_to_snapshot_keeps_prior_variant_changes() -> None:
    item_snapshot: dict = {
        "id": "item-1",
        "variants": [
            {
                "variant_id": "v-original",
                "sku": "10005",
                "cost": 0.75,
                "barcode": "6000000000001",
            },
            {
                "variant_id": "v-paprika",
                "sku": "10006",
                "cost": 0.70,
                "barcode": "6000000000002",
            },
        ],
    }

    _apply_successful_variant_update_to_snapshot(item_snapshot, "v-original", 0.81, "0123456789012")
    _apply_successful_variant_update_to_snapshot(item_snapshot, "v-paprika", 0.79, "0999999999999")

    variants = item_snapshot["variants"]
    assert isinstance(variants, list)
    by_variant_id = {variant["variant_id"]: variant for variant in variants}

    assert by_variant_id["v-original"]["cost"] == 0.81
    assert by_variant_id["v-original"]["barcode"] == "0123456789012"
    assert by_variant_id["v-paprika"]["cost"] == 0.79
    assert by_variant_id["v-paprika"]["barcode"] == "0999999999999"


def test_apply_successful_variant_update_to_snapshot_keeps_existing_barcode_when_new_barcode_is_none() -> None:
    item_snapshot: dict = {
        "id": "item-1",
        "variants": [
            {
                "variant_id": "v-original",
                "sku": "10005",
                "cost": 0.75,
                "barcode": "6000000000001",
            },
        ],
    }

    _apply_successful_variant_update_to_snapshot(item_snapshot, "v-original", 0.81, None)

    variant = item_snapshot["variants"][0]
    assert variant["cost"] == 0.81
    assert variant["barcode"] == "6000000000001"


def test_apply_successful_variant_update_to_snapshot_noops_for_unknown_variant() -> None:
    item_snapshot: dict = {
        "id": "item-1",
        "variants": [
            {
                "variant_id": "v-original",
                "sku": "10005",
                "cost": 0.75,
                "barcode": "6000000000001",
            },
        ],
    }

    _apply_successful_variant_update_to_snapshot(item_snapshot, "v-missing", 0.81, "0123456789012")

    variant = item_snapshot["variants"][0]
    assert variant["cost"] == 0.75
    assert variant["barcode"] == "6000000000001"
