import hashlib
import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

import product_images
from product_images import ProductImageSyncSummary


def test_build_targets_from_loyverse_items_includes_only_on_sale() -> None:
    items = [
        {
            "id": "item-1",
            "item_name": "Vodka",
            "variants": [{"stores": [{"available_for_sale": True}]}],
        },
        {
            "id": "item-2",
            "item_name": "Old stock",
            "variants": [{"stores": [{"available_for_sale": False}]}],
        },
    ]

    targets = product_images._build_targets_from_loyverse_items(items)

    assert [(target.item_id, target.name) for target in targets] == [("item-1", "Vodka")]


def test_build_targets_from_loyverse_items_marks_products_with_variants() -> None:
    items = [
        {
            "id": "item-1",
            "item_name": "Vodka",
            "variants": [
                {"stores": [{"available_for_sale": True}]},
                {"stores": [{"available_for_sale": True}]},
            ],
        },
        {
            "id": "item-2",
            "item_name": "Whisky",
            "variants": [{"stores": [{"available_for_sale": True}]}],
        },
    ]

    targets = product_images._build_targets_from_loyverse_items(items)

    assert [(target.item_id, target.has_variants) for target in targets] == [
        ("item-1", True),
        ("item-2", False),
    ]


def test_run_product_image_sync_dry_run_builds_local_files(monkeypatch, tmp_path: Path) -> None:
    products_yaml = tmp_path / "products.yaml"
    products_yaml.write_text(
        "defaults:\n  width: 120\n  height: 120\n",
    )

    monkeypatch.setattr(product_images, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(
        product_images,
        "get_loyverse_items",
        lambda: [{"id": "item-1", "item_name": "Vodka", "variants": [{"stores": []}]}],
    )

    summary = product_images.run_product_image_sync(products_file=products_yaml, write=False)

    assert summary == ProductImageSyncSummary(
        built_images=1,
        uploaded_images=0,
        upload_failures=0,
        new_product_ids_added=1,
    )
    expected_output_dir = tmp_path / "outputs" / "product-images"
    assert (expected_output_dir / "item-1.png").exists()
    seeded_yaml = products_yaml.read_text()
    assert "product_id_overrides:" in seeded_yaml
    assert "item-1" in seeded_yaml
    assert "Vodka" in seeded_yaml


def test_run_product_image_sync_keeps_stale_pngs(monkeypatch, tmp_path: Path) -> None:
    products_yaml = tmp_path / "products.yaml"
    products_yaml.write_text("defaults:\n  width: 120\n  height: 120\n")

    output_dir = tmp_path / "outputs" / "product-images"
    output_dir.mkdir(parents=True)
    stale_file = output_dir / "item-1.png"
    stale_file.write_bytes(b"stale")

    monkeypatch.setattr(product_images, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(
        product_images,
        "get_loyverse_items",
        lambda: [
            {
                "id": "item-2",
                "item_name": "Vodka",
                "variants": [{"stores": []}],
            },
        ],
    )

    summary = product_images.run_product_image_sync(products_file=products_yaml, write=False)

    assert summary.built_images == 1
    assert stale_file.exists()
    assert (output_dir / "item-2.png").exists()


def test_run_product_image_sync_write_uploads_each_on_sale_item(monkeypatch, tmp_path: Path) -> None:
    products_yaml = tmp_path / "products.yaml"
    products_yaml.write_text("defaults: {}\n")

    monkeypatch.setattr(product_images, "OUTPUT_DIR", tmp_path / "outputs")

    monkeypatch.setattr(
        product_images,
        "get_loyverse_items",
        lambda: [
            {
                "id": "item-1",
                "item_name": "Product A",
                "variants": [{"stores": []}],
            },
            {
                "id": "item-2",
                "item_name": "Product B",
                "variants": [{"stores": [{"available_for_sale": True}]}],
            },
            {
                "id": "item-3",
                "item_name": "Product C",
                "variants": [{"stores": [{"available_for_sale": False}]}],
            },
            {
                "id": "item-1",
                "item_name": "Product A Duplicate",
                "variants": [{"stores": []}],
            },
        ],
    )

    uploaded_item_ids: list[str] = []

    def fake_upload_item_image(item_id: str, image_bytes: bytes) -> tuple[bool, str]:
        assert image_bytes
        uploaded_item_ids.append(item_id)
        return True, "uploaded"

    monkeypatch.setattr(product_images, "upload_item_image", fake_upload_item_image)

    summary = product_images.run_product_image_sync(products_file=products_yaml, write=True)

    assert uploaded_item_ids == ["item-1", "item-2"]
    assert summary.built_images == 2
    assert summary.uploaded_images == 2
    assert summary.upload_failures == 0
    assert summary.new_product_ids_added == 2


def test_style_for_product_id_uses_overrides() -> None:
    style = product_images.ProductImageStyle(
        width=100,
        height=100,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=12,
        subtitle_font_size=10,
    )
    config = {
        "product_id_overrides": {
            "item-1": {
                "background_colour": "#008000",
                "title_font_size": 18,
            },
        },
    }

    styled = product_images._style_for_product_id(config, style, "item-1")

    assert styled.background_color == "#008000"
    assert styled.title_font_size == 18
    assert styled.height == 100


def test_style_from_config_supports_palette_tokens() -> None:
    config = {
        "palette": {
            "brand_bg": "#123456",
            "brand_text": "#f0f0f0",
            "brand_accent": "#ff6600",
        },
        "defaults": {
            "background_colour": "brand_bg",
            "text_colour": "$brand_text",
            "accent_colour": "brand_accent",
        },
    }

    style = product_images._style_from_config(config)

    assert style.background_color == "#123456"
    assert style.text_color == "#f0f0f0"
    assert style.accent_color == "#ff6600"


def test_style_from_config_supports_colour_keys() -> None:
    config = {
        "defaults": {
            "background_colour": "#123456",
            "text_colour": "#eeeeee",
            "accent_colour": "#ff6600",
        },
    }

    style = product_images._style_from_config(config)

    assert style.background_color == "#123456"
    assert style.text_color == "#eeeeee"
    assert style.accent_color == "#ff6600"


def test_style_from_config_supports_icon_tokens() -> None:
    config = {
        "icons": {
            "pint": {
                "icon": "U+F79F",
                "icon_font_path": "fonts/solid.otf",
                "scale": 0.5,
                "tilted": True,
            },
        },
        "defaults": {
            "icon": "pint",
        },
    }

    style = product_images._style_from_config(config)

    assert style.icon == "U+F79F"
    assert style.icon_font_path == "fonts/solid.otf"
    assert style.icon_scale == 0.5
    assert style.icon_tilted is True


def test_style_for_product_id_supports_palette_tokens() -> None:
    base_style = product_images.ProductImageStyle(
        width=100,
        height=100,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=12,
        subtitle_font_size=10,
    )
    config = {
        "palette": {
            "oak": "#5b4636",
        },
        "product_id_overrides": {
            "item-1": {
                "background_colour": "$oak",
            },
        },
    }

    style = product_images._style_for_product_id(config, base_style, "item-1")

    assert style.background_color == "#5b4636"


def test_style_for_product_id_supports_colour_keys() -> None:
    base_style = product_images.ProductImageStyle(
        width=100,
        height=100,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=12,
        subtitle_font_size=10,
    )
    config = {
        "product_id_overrides": {
            "item-1": {
                "background_colour": "#0f172a",
                "text_colour": "#e2e8f0",
                "accent_colour": "#f59e0b",
            },
        },
    }

    style = product_images._style_for_product_id(config, base_style, "item-1")

    assert style.background_color == "#0f172a"
    assert style.text_color == "#e2e8f0"
    assert style.accent_color == "#f59e0b"


def test_style_for_product_id_supports_icon_tokens() -> None:
    base_style = product_images.ProductImageStyle(
        width=100,
        height=100,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=12,
        subtitle_font_size=10,
    )
    config = {
        "icons": {
            "guinness": {
                "icon": "U+F79F",
                "icon_font_path": "fonts/solid.otf",
                "scale": 0.5,
                "tilted": True,
            },
        },
        "product_id_overrides": {
            "item-1": {
                "icon": "guinness",
            },
        },
    }

    style = product_images._style_for_product_id(config, base_style, "item-1")

    assert style.icon == "U+F79F"
    assert style.icon_font_path == "fonts/solid.otf"
    assert style.icon_scale == 0.5
    assert style.icon_tilted is True


def test_style_for_product_id_can_override_icon_scale() -> None:
    base_style = product_images.ProductImageStyle(
        width=100,
        height=100,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=12,
        subtitle_font_size=10,
        icon="U+F79F",
        icon_font_path="fonts/solid.otf",
        icon_scale=0.5,
    )
    config = {
        "product_id_overrides": {
            "item-1": {
                "scale": 1.0,
            },
        },
    }

    style = product_images._style_for_product_id(config, base_style, "item-1")

    assert style.icon_scale == 1.0


def test_style_for_product_id_can_override_icon_tilted() -> None:
    base_style = product_images.ProductImageStyle(
        width=100,
        height=100,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=12,
        subtitle_font_size=10,
        icon="U+F79F",
        icon_font_path="fonts/solid.otf",
        icon_tilted=True,
    )
    config = {
        "product_id_overrides": {
            "item-1": {
                "tilted": False,
            },
        },
    }

    style = product_images._style_for_product_id(config, base_style, "item-1")

    assert style.icon_tilted is False


def test_style_for_product_id_can_enable_af_flag_via_flags() -> None:
    base_style = product_images.ProductImageStyle(
        width=100,
        height=100,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=12,
        subtitle_font_size=10,
    )
    config = {
        "palette": {
            "sky_blue": "#82C8E5",
        },
        "flags": {
            "af": {"text": "AF", "value": "sky_blue"},
        },
        "product_id_overrides": {
            "item-1": {
                "flags": ["af"],
            },
        },
    }

    style = product_images._style_for_product_id(config, base_style, "item-1")

    assert style.flags == (("AF", "#82C8E5"),)


def test_style_for_product_id_orders_flags_by_top_level_definition() -> None:
    base_style = product_images.ProductImageStyle(
        width=100,
        height=100,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=12,
        subtitle_font_size=10,
    )
    config = {
        "palette": {
            "sky_blue": "#82C8E5",
            "medium_green": "#508a3d",
        },
        "flags": {
            "af": {"text": "AF", "value": "sky_blue"},
            "new": {"text": "NEW", "value": "medium_green"},
        },
        "product_id_overrides": {
            "item-1": {
                "flags": ["new", "af"],
            },
        },
    }

    style = product_images._style_for_product_id(config, base_style, "item-1")

    assert style.flags == (("AF", "#82C8E5"), ("NEW", "#508a3d"))


def test_initials_from_name_handles_single_word() -> None:
    assert product_images._initials_from_name("Vodka") == "VO"


def test_initials_from_name_handles_multi_word() -> None:
    assert product_images._initials_from_name("Gordon's Pink Gin") == "GPG"


def test_initials_from_name_handles_empty_name() -> None:
    assert product_images._initials_from_name("  ") == "?"


def test_resolve_icon_glyph_accepts_hex_codepoint_forms() -> None:
    assert product_images._resolve_icon_glyph("f79f") == "\uf79f"
    assert product_images._resolve_icon_glyph("U+F79F") == "\uf79f"
    assert product_images._resolve_icon_glyph("\\uf79f") == "\uf79f"


def test_resolve_icon_glyph_keeps_literal_single_glyph() -> None:
    assert product_images._resolve_icon_glyph("\uf7b6") == "\uf7b6"


def test_darken_color_reduces_brightness_by_requested_amount() -> None:
    assert product_images._darken_color("#808080", amount=0.6) == "#333333"


def test_render_image_bytes_raises_when_icon_font_path_missing() -> None:
    style = product_images.ProductImageStyle(
        width=240,
        height=240,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=24,
        subtitle_font_size=16,
        icon="U+F79F",
        icon_font_path=None,
    )

    with pytest.raises(ValueError, match="icon_font_path"):
        product_images._render_image_bytes(
            product_images.ProductImageTarget(item_id="item-1", name="Vodka"),
            style,
        )


def test_render_image_bytes_does_not_depend_on_item_id() -> None:
    style = product_images.ProductImageStyle(
        width=240,
        height=240,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=24,
        subtitle_font_size=16,
    )

    image_a = product_images._render_image_bytes(
        product_images.ProductImageTarget(item_id="item-1", name="Vodka"),
        style,
    )
    image_b = product_images._render_image_bytes(
        product_images.ProductImageTarget(item_id="item-2", name="Vodka"),
        style,
    )

    assert image_a == image_b


def test_render_image_bytes_draws_top_right_af_flag_only_when_configured() -> None:
    base_style = product_images.ProductImageStyle(
        width=200,
        height=200,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=24,
        subtitle_font_size=16,
    )
    af_style = product_images.ProductImageStyle(
        width=200,
        height=200,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=24,
        subtitle_font_size=16,
        flags=(("AF", "#82C8E5"),),
    )

    standard_image_bytes = product_images._render_image_bytes(
        product_images.ProductImageTarget(item_id="item-1", name="Vodka"),
        base_style,
    )
    af_image_bytes = product_images._render_image_bytes(
        product_images.ProductImageTarget(item_id="item-2", name="Vodka (AF)"),
        af_style,
    )

    standard_image = Image.open(BytesIO(standard_image_bytes)).convert("RGB")
    af_image = Image.open(BytesIO(af_image_bytes)).convert("RGB")

    probe = (153, 0)
    assert standard_image.getpixel(probe) == (17, 17, 17)
    assert af_image.getpixel(probe) != (17, 17, 17)


def test_render_image_bytes_draws_top_right_variants_flag_only_when_target_has_variants() -> None:
    style = product_images.ProductImageStyle(
        width=200,
        height=200,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=24,
        subtitle_font_size=16,
    )

    standard_image_bytes = product_images._render_image_bytes(
        product_images.ProductImageTarget(item_id="item-1", name="Vodka", has_variants=False),
        style,
    )
    variants_image_bytes = product_images._render_image_bytes(
        product_images.ProductImageTarget(item_id="item-2", name="Vodka", has_variants=True),
        style,
    )

    standard_image = Image.open(BytesIO(standard_image_bytes)).convert("RGB")
    variants_image = Image.open(BytesIO(variants_image_bytes)).convert("RGB")

    probe = (169, 0)
    assert standard_image.getpixel(probe) == (17, 17, 17)
    assert variants_image.getpixel(probe) != (17, 17, 17)


def test_render_image_bytes_tilted_icon_differs_from_non_tilted(monkeypatch) -> None:
    monkeypatch.setattr(product_images, "_get_icon_font", lambda _path, size: product_images._get_font(size))

    base_style = product_images.ProductImageStyle(
        width=220,
        height=220,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=28,
        subtitle_font_size=16,
        icon="A",
        icon_font_path="fonts/solid.otf",
        icon_tilted=False,
    )
    tilted_style = product_images.ProductImageStyle(
        width=220,
        height=220,
        background_color="#111111",
        text_color="#ffffff",
        accent_color="#ff0000",
        title_font_size=28,
        subtitle_font_size=16,
        icon="A",
        icon_font_path="fonts/solid.otf",
        icon_tilted=True,
    )

    standard_image_bytes = product_images._render_image_bytes(
        product_images.ProductImageTarget(item_id="item-1", name="Vodka"),
        base_style,
    )
    tilted_image_bytes = product_images._render_image_bytes(
        product_images.ProductImageTarget(item_id="item-1", name="Vodka"),
        tilted_style,
    )

    assert tilted_image_bytes != standard_image_bytes


def test_seed_new_product_ids_rewrites_sorted_by_product_name(tmp_path: Path) -> None:
    products_yaml = tmp_path / "products.yaml"
    products_yaml.write_text(
        "product_id_overrides:\n"
        "  item-b: # Zeta\n"
        "    background_color: '#222222'\n"
        "  item-a: # Alpha\n"
        "    background_color: '#111111'\n",
    )

    products = [
        product_images.ProductImageTarget(item_id="item-b", name="Zeta"),
        product_images.ProductImageTarget(item_id="item-a", name="Alpha"),
        product_images.ProductImageTarget(item_id="item-c", name="Beta"),
    ]

    new_count = product_images._seed_new_product_ids(products_yaml, products)

    assert new_count == 1
    rewritten = products_yaml.read_text()
    assert rewritten.index("item-a") < rewritten.index("item-c") < rewritten.index("item-b")
    assert "item-a:" in rewritten
    assert "# Alpha" in rewritten
    assert "item-c:" in rewritten
    assert "# Beta" in rewritten
    assert "item-b:" in rewritten
    assert "# Zeta" in rewritten
    assert "background_color: '#111111'" in rewritten
    assert "background_color: '#222222'" in rewritten


def test_run_product_image_sync_creates_cache_file_on_write(monkeypatch, tmp_path: Path) -> None:
    products_yaml = tmp_path / "products.yaml"
    products_yaml.write_text("defaults: {}\n")

    output_dir = tmp_path / "outputs" / "product-images"
    monkeypatch.setattr(product_images, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(
        product_images,
        "get_loyverse_items",
        lambda: [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [{"stores": []}],
            },
        ],
    )

    def fake_upload_item_image(item_id: str, image_bytes: bytes) -> tuple[bool, str]:
        return True, "ok"

    monkeypatch.setattr(product_images, "upload_item_image", fake_upload_item_image)

    product_images.run_product_image_sync(products_file=products_yaml, write=True)

    cache_file = output_dir / ".image_cache.json"
    assert cache_file.exists()

    cache_data = json.loads(cache_file.read_text())
    assert "item-1" in cache_data
    assert isinstance(cache_data["item-1"], str)
    assert len(cache_data["item-1"]) == 64  # SHA256 hex is 64 chars


def test_run_product_image_sync_skips_unchanged_images(monkeypatch, tmp_path: Path) -> None:
    products_yaml = tmp_path / "products.yaml"
    products_yaml.write_text("defaults: {}\n")

    output_dir = tmp_path / "outputs" / "product-images"
    output_dir.mkdir(parents=True)

    # Pre-populate cache with item-1
    cache_file = output_dir / ".image_cache.json"
    first_render_bytes = b"test image content 1"
    first_hash = hashlib.sha256(first_render_bytes).hexdigest()
    cache_file.write_text(json.dumps({"item-1": first_hash}))

    monkeypatch.setattr(product_images, "OUTPUT_DIR", tmp_path / "outputs")

    # Mock _render_image_bytes to return consistent content
    def mock_render_with_same_content(
        target: product_images.ProductImageTarget,
        style: product_images.ProductImageStyle,
    ) -> bytes:
        return first_render_bytes

    monkeypatch.setattr(product_images, "_render_image_bytes", mock_render_with_same_content)

    monkeypatch.setattr(
        product_images,
        "get_loyverse_items",
        lambda: [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [{"stores": []}],
            },
        ],
    )

    upload_count = 0

    def fake_upload_item_image(item_id: str, image_bytes: bytes) -> tuple[bool, str]:
        nonlocal upload_count
        upload_count += 1
        return True, "ok"

    monkeypatch.setattr(product_images, "upload_item_image", fake_upload_item_image)

    summary = product_images.run_product_image_sync(products_file=products_yaml, write=True)

    assert upload_count == 0  # Should skip because hash matches
    assert summary.uploaded_images == 0


def test_run_product_image_sync_uploads_changed_images(monkeypatch, tmp_path: Path) -> None:
    products_yaml = tmp_path / "products.yaml"
    products_yaml.write_text("defaults: {}\n")

    output_dir = tmp_path / "outputs" / "product-images"
    output_dir.mkdir(parents=True)

    # Pre-populate cache with old hash
    cache_file = output_dir / ".image_cache.json"
    old_hash = hashlib.sha256(b"old content").hexdigest()
    cache_file.write_text(json.dumps({"item-1": old_hash}))

    monkeypatch.setattr(product_images, "OUTPUT_DIR", tmp_path / "outputs")

    # Mock _render_image_bytes to return new content
    new_render_bytes = b"new image content different from old"

    def mock_render_with_new_content(
        target: product_images.ProductImageTarget,
        style: product_images.ProductImageStyle,
    ) -> bytes:
        return new_render_bytes

    monkeypatch.setattr(product_images, "_render_image_bytes", mock_render_with_new_content)

    monkeypatch.setattr(
        product_images,
        "get_loyverse_items",
        lambda: [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [{"stores": []}],
            },
        ],
    )

    upload_count = 0

    def fake_upload_item_image(item_id: str, image_bytes: bytes) -> tuple[bool, str]:
        nonlocal upload_count
        upload_count += 1
        return True, "ok"

    monkeypatch.setattr(product_images, "upload_item_image", fake_upload_item_image)

    summary = product_images.run_product_image_sync(products_file=products_yaml, write=True)

    assert upload_count == 1  # Should upload because hash differs
    assert summary.uploaded_images == 1

    # Verify cache is updated with new hash
    updated_cache = json.loads(cache_file.read_text())
    new_hash = hashlib.sha256(new_render_bytes).hexdigest()
    assert updated_cache["item-1"] == new_hash


def test_run_product_image_sync_ignores_non_dict_cache_payload(monkeypatch, tmp_path: Path) -> None:
    products_yaml = tmp_path / "products.yaml"
    products_yaml.write_text("defaults: {}\n")

    output_dir = tmp_path / "outputs" / "product-images"
    output_dir.mkdir(parents=True)

    cache_file = output_dir / ".image_cache.json"
    cache_file.write_text("[]")

    monkeypatch.setattr(product_images, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(
        product_images,
        "get_loyverse_items",
        lambda: [
            {
                "id": "item-1",
                "item_name": "Vodka",
                "variants": [{"stores": []}],
            },
        ],
    )

    uploaded_item_ids: list[str] = []

    def fake_upload_item_image(item_id: str, image_bytes: bytes) -> tuple[bool, str]:
        assert image_bytes
        uploaded_item_ids.append(item_id)
        return True, "uploaded"

    monkeypatch.setattr(product_images, "upload_item_image", fake_upload_item_image)

    summary = product_images.run_product_image_sync(products_file=products_yaml, write=True)

    assert uploaded_item_ids == ["item-1"]
    assert summary.uploaded_images == 1
