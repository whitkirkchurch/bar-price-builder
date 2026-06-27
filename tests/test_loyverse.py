"""Unit tests for loyverse.py till product building and cost extraction."""

from loyverse import build_till_products, extract_variant_cost_pence


class TestTillProductBuilding:
    """Tests for build_till_products and variant handling."""

    def test_build_till_products_extracts_plu_and_price(self) -> None:
        items = [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [
                    {
                        "variant_id": "v-1",
                        "sku": "1001",
                        "default_price": 12.50,
                        "stores": [{"available_for_sale": True}],
                    },
                ],
            },
        ]
        products = build_till_products(items)
        assert 1001 in products
        assert products[1001]["plu"] == 1001
        assert products[1001]["name"] == "Vodka"
        assert products[1001]["price"] == 1250

    def test_build_till_products_skips_non_numeric_sku(self) -> None:
        items = [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [
                    {
                        "variant_id": "v-1",
                        "sku": "ABC123",
                        "default_price": 12.50,
                    },
                ],
            },
        ]
        products = build_till_products(items)
        assert len(products) == 0

    def test_build_till_products_handles_availability(self) -> None:
        items = [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [
                    {
                        "variant_id": "v-1",
                        "sku": "1001",
                        "default_price": 12.50,
                        "stores": [{"available_for_sale": False}],
                    },
                ],
            },
        ]
        products = build_till_products(items)
        assert products[1001]["available_for_sale"] is False

    def test_build_till_products_handles_barcode_none(self) -> None:
        items = [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [
                    {
                        "variant_id": "v-1",
                        "sku": "1001",
                        "default_price": 12.50,
                        "barcode": None,
                    },
                ],
            },
        ]
        products = build_till_products(items)
        assert products[1001]["barcode"] is None

    def test_build_till_products_treats_missing_default_price_as_unset(self) -> None:
        items = [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [
                    {
                        "variant_id": "v-1",
                        "sku": "1001",
                    },
                ],
            },
        ]
        products = build_till_products(items)
        assert products[1001]["till_price_set"] is False
        assert products[1001]["price"] == 0

    def test_build_till_products_strips_whitespace_from_barcode(self) -> None:
        items = [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [
                    {
                        "variant_id": "v-1",
                        "sku": "1001",
                        "default_price": 12.50,
                        "barcode": "  5053990107339  ",
                    },
                ],
            },
        ]
        products = build_till_products(items)
        assert products[1001]["barcode"] == "5053990107339"


class TestVariantCostExtraction:
    """Tests for extract_variant_cost_pence."""

    def test_extract_variant_cost_pence_from_cost_field(self) -> None:
        variant = {"cost": 0.75}
        assert extract_variant_cost_pence(variant) == 75.0

    def test_extract_variant_cost_pence_from_supply_price(self) -> None:
        variant = {"supply_price": 0.50}
        assert extract_variant_cost_pence(variant) == 50.0

    def test_extract_variant_cost_pence_from_default_cost(self) -> None:
        variant = {"default_cost": 0.60}
        assert extract_variant_cost_pence(variant) == 60.0

    def test_extract_variant_cost_pence_prefers_cost(self) -> None:
        variant = {"cost": 0.75, "supply_price": 0.50}
        assert extract_variant_cost_pence(variant) == 75.0

    def test_extract_variant_cost_pence_returns_none_for_missing(self) -> None:
        variant = {"other_field": "value"}
        assert extract_variant_cost_pence(variant) is None

    def test_extract_variant_cost_pence_returns_none_for_invalid(self) -> None:
        variant = {"cost": "not-a-number"}
        assert extract_variant_cost_pence(variant) is None
