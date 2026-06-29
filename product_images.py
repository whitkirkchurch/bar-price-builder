import hashlib
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import click
from PIL import Image, ImageColor, ImageDraw, ImageFont
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap

from config import OUTPUT_DIR
from loyverse import get_loyverse_items, upload_item_image

YAML_SAFE = YAML(typ="safe")
YAML_RT = YAML()
YAML_RT.indent(mapping=2, sequence=4, offset=2)
YAML_RT.preserve_quotes = True

SAFE_MARGIN_TOP_PCT = 0.12
SAFE_MARGIN_SIDE_PCT = 0.04
SAFE_MARGIN_BOTTOM_CLEAR_PCT = 0.42


@dataclass(frozen=True)
class ProductImageStyle:
    width: int
    height: int
    background_color: str
    text_color: str
    accent_color: str
    title_font_size: int
    subtitle_font_size: int
    icon: str | None = None
    icon_font_path: str | None = None
    icon_scale: float = 1.0
    icon_tilted: bool = False
    icon_highlight_color: str | None = None
    flag_keys: tuple[str, ...] = ()
    flags: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ProductImageTarget:
    item_id: str
    name: str
    has_variants: bool = False


@dataclass(frozen=True)
class ProductImageSyncSummary:
    built_images: int
    uploaded_images: int
    upload_failures: int
    new_product_ids_added: int


@dataclass(frozen=True)
class TiltedIconRenderParams:
    content: str
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont
    bbox: tuple[float, float, float, float]
    text_width: float
    text_height: float
    text_x: float
    text_y: float
    text_stroke_width: int
    text_stroke_color: str


def _load_products_config(products_file: Path) -> dict[str, Any]:
    with products_file.open() as file_handle:
        config_data = YAML_SAFE.load(file_handle) or {}
    if not isinstance(config_data, dict):
        msg = f"Expected mapping at top level of {products_file}"
        raise TypeError(msg)
    return config_data


def _load_products_config_document(products_file: Path) -> CommentedMap:
    with products_file.open() as file_handle:
        config_doc = YAML_RT.load(file_handle) or CommentedMap()

    if not isinstance(config_doc, CommentedMap):
        msg = f"Expected mapping at top level of {products_file}"
        raise TypeError(msg)

    return config_doc


def _safe_color(value: Any, fallback: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback
    candidate = value.strip()
    try:
        ImageColor.getrgb(candidate)
    except ValueError:
        return fallback
    return candidate


def _resolve_color(value: Any, fallback: str, palette: dict[str, str]) -> str:
    if not isinstance(value, str) or not value.strip():
        return fallback

    raw_value = value.strip()
    palette_key = raw_value[1:] if raw_value.startswith("$") else raw_value

    if palette_key in palette:
        return _safe_color(palette[palette_key], fallback)

    return _safe_color(raw_value, fallback)


def _darken_color(color: str, amount: float = 0.6) -> str:
    """Darken a color by the provided amount, where 0.6 means 60% darker."""
    safe_amount = min(max(amount, 0.0), 1.0)
    red, green, blue = ImageColor.getrgb(_safe_color(color, "#000000"))
    multiplier = 1.0 - safe_amount
    darkened = (
        int(red * multiplier),
        int(green * multiplier),
        int(blue * multiplier),
    )
    return f"#{darkened[0]:02x}{darkened[1]:02x}{darkened[2]:02x}"


def _parse_palette(raw_config: dict[str, Any]) -> dict[str, str]:
    raw_palette = raw_config.get("palette", {})
    if not isinstance(raw_palette, dict):
        return {}

    palette: dict[str, str] = {}
    for key, value in raw_palette.items():
        if not isinstance(key, str) or not key.strip():
            continue
        resolved = _safe_color(value, "")
        if not resolved:
            continue
        palette[key.strip()] = resolved

    return palette


def _parse_icons(raw_config: dict[str, Any]) -> dict[str, dict[str, str | float | bool | None]]:
    raw_icons = raw_config.get("icons", {})
    if not isinstance(raw_icons, dict):
        return {}

    icons: dict[str, dict[str, str | float | bool | None]] = {}
    for key, value in raw_icons.items():
        if not isinstance(key, str) or not key.strip():
            continue
        token = key.strip()
        if isinstance(value, str) and value.strip():
            icons[token] = {
                "icon": value.strip(),
                "icon_font_path": None,
                "icon_scale": 1.0,
                "icon_tilted": False,
                "icon_highlight_color": None,
            }
            continue
        if isinstance(value, dict):
            raw_icon = value.get("icon")
            if not isinstance(raw_icon, str) or not raw_icon.strip():
                continue
            raw_icon_font_path = value.get("icon_font_path")
            icon_font_path = (
                raw_icon_font_path.strip()
                if isinstance(raw_icon_font_path, str) and raw_icon_font_path.strip()
                else None
            )
            icon_scale = float(value.get("scale", 1.0)) if isinstance(value.get("scale"), (int, float)) else 1.0
            icon_tilted = _as_bool(value.get("tilted"), False)
            icon_highlight_color = value.get("highlight_colour")
            if not isinstance(icon_highlight_color, str) or not icon_highlight_color.strip():
                icon_highlight_color = None
            icons[token] = {
                "icon": raw_icon.strip(),
                "icon_font_path": icon_font_path,
                "icon_scale": icon_scale,
                "icon_tilted": icon_tilted,
                "icon_highlight_color": icon_highlight_color,
            }

    return icons


def _parse_flags(raw_config: dict[str, Any], palette: dict[str, str]) -> dict[str, tuple[str, str]]:
    raw_flags = raw_config.get("flags", {})
    if not isinstance(raw_flags, dict):
        return {}

    flags: dict[str, tuple[str, str]] = {}
    for key, value in raw_flags.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, dict):
            continue
        token = key.strip()
        text = value.get("text")
        color_value = value.get("value")
        if not isinstance(text, str) or not text.strip():
            continue
        if not isinstance(color_value, str) or not color_value.strip():
            continue
        flags[token] = (text.strip(), _resolve_color(color_value, "#1e88e5", palette))

    return flags


def _parse_flag_keys(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    keys: set[str] = set()
    for item in value:
        if isinstance(item, str) and item.strip():
            keys.add(item.strip())
    return keys


def _ordered_flag_specs(
    flag_keys: set[str],
    flag_definitions: dict[str, tuple[str, str]],
) -> tuple[tuple[str, str], ...]:
    ordered: list[tuple[str, str]] = []
    for key, spec in flag_definitions.items():
        if key in flag_keys:
            ordered.append(spec)
    return tuple(ordered)


def _resolve_icon(  # noqa: PLR0913
    value: Any,
    fallback: str | None,
    icons: dict[str, dict[str, str | float | bool | None]],
    *,
    fallback_scale: float = 1.0,
    fallback_tilted: bool = False,
    fallback_highlight_color: str | None = None,
) -> tuple[str | None, str | None, float, bool, str | None]:
    if not isinstance(value, str) or not value.strip():
        return fallback, None, fallback_scale, fallback_tilted, fallback_highlight_color

    raw_value = value.strip()
    icon_key = raw_value

    if icon_key in icons:
        icon_def = icons[icon_key]
        icon_value = icon_def.get("icon")
        icon_font_path = icon_def.get("icon_font_path")
        icon_scale = icon_def.get("icon_scale")
        icon_tilted = icon_def.get("icon_tilted")
        icon_highlight_color = icon_def.get("icon_highlight_color")
        return (
            icon_value if isinstance(icon_value, str) else fallback,
            icon_font_path if isinstance(icon_font_path, str) else None,
            icon_scale if isinstance(icon_scale, float) else fallback_scale,
            icon_tilted if isinstance(icon_tilted, bool) else fallback_tilted,
            icon_highlight_color if isinstance(icon_highlight_color, str) else fallback_highlight_color,
        )

    return raw_value, None, fallback_scale, fallback_tilted, fallback_highlight_color


def _as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "on"}:
            return True
        if normalized in {"false", "no", "0", "off"}:
            return False
    return fallback


def _as_int(value: Any, fallback: int) -> int:
    if value is None:
        return fallback
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _style_from_config(raw_config: dict[str, Any]) -> ProductImageStyle:
    defaults = raw_config.get("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
    palette = _parse_palette(raw_config)
    icons = _parse_icons(raw_config)
    flag_definitions = _parse_flags(raw_config, palette)
    (
        resolved_icon,
        resolved_icon_font_path,
        resolved_icon_scale,
        resolved_icon_tilted,
        resolved_icon_highlight_color_token,
    ) = _resolve_icon(
        defaults.get("icon"),
        None,
        icons,
    )
    configured_icon_font_path = (
        defaults.get("icon_font_path") if isinstance(defaults.get("icon_font_path"), str) else None
    )
    scale_value = defaults.get("scale")
    configured_icon_scale = float(scale_value) if isinstance(scale_value, (int, float)) else None
    configured_icon_tilted = _as_bool(defaults.get("tilted"), False) if "tilted" in defaults else None
    configured_icon_highlight_color_token = defaults.get("highlight_colour")
    if not isinstance(configured_icon_highlight_color_token, str) or not configured_icon_highlight_color_token.strip():
        configured_icon_highlight_color_token = None

    # Resolve highlight color through palette if it's a token, otherwise treat as hex
    highlight_token = (
        configured_icon_highlight_color_token
        if configured_icon_highlight_color_token is not None
        else resolved_icon_highlight_color_token
    )
    resolved_icon_highlight_color = _resolve_color(highlight_token, "#FFFFFF", palette) if highlight_token else None

    configured_flag_keys = _parse_flag_keys(defaults.get("flags"))
    ordered_flags = _ordered_flag_specs(configured_flag_keys, flag_definitions)

    return ProductImageStyle(
        width=_as_int(defaults.get("width"), 1200),
        height=_as_int(defaults.get("height"), 1200),
        background_color=_resolve_color(defaults.get("background_colour"), "#0f172a", palette),
        text_color=_resolve_color(defaults.get("text_colour"), "#f8fafc", palette),
        accent_color=_resolve_color(defaults.get("accent_colour"), "#f59e0b", palette),
        title_font_size=_as_int(defaults.get("title_font_size"), 84),
        subtitle_font_size=_as_int(defaults.get("subtitle_font_size"), 42),
        icon=resolved_icon,
        icon_font_path=configured_icon_font_path or resolved_icon_font_path,
        icon_scale=configured_icon_scale if configured_icon_scale is not None else resolved_icon_scale,
        icon_tilted=configured_icon_tilted if configured_icon_tilted is not None else resolved_icon_tilted,
        icon_highlight_color=resolved_icon_highlight_color,
        flag_keys=tuple(sorted(configured_flag_keys)),
        flags=ordered_flags,
    )


def _variant_is_on_sale(variant: dict[str, Any]) -> bool:
    stores = variant.get("stores", [])
    if not stores:
        return True
    return any(store.get("available_for_sale", False) for store in stores)


def _build_targets_from_loyverse_items(items: list[dict[str, Any]]) -> list[ProductImageTarget]:
    targets: list[ProductImageTarget] = []
    seen_item_ids: set[str] = set()

    for item in items:
        item_id = str(item.get("id", "")).strip()
        if not item_id or item_id in seen_item_ids:
            continue

        item_name = str(item.get("item_name", "")).strip() or "Unnamed product"
        variants = item.get("variants", [])
        if not isinstance(variants, list) or not variants:
            continue

        if not any(isinstance(variant, dict) and _variant_is_on_sale(variant) for variant in variants):
            continue

        variant_count = sum(1 for variant in variants if isinstance(variant, dict))

        targets.append(ProductImageTarget(item_id=item_id, name=item_name, has_variants=variant_count > 1))
        seen_item_ids.add(item_id)

    return sorted(targets, key=lambda target: target.name.lower())


def _style_for_product_id(raw_config: dict[str, Any], base_style: ProductImageStyle, item_id: str) -> ProductImageStyle:
    overrides = raw_config.get("product_id_overrides", {})
    if not isinstance(overrides, dict):
        return base_style
    override = overrides.get(item_id, {})
    if not isinstance(override, dict):
        return base_style
    palette = _parse_palette(raw_config)
    icons = _parse_icons(raw_config)
    flag_definitions = _parse_flags(raw_config, palette)
    (
        resolved_icon,
        resolved_icon_font_path,
        resolved_icon_scale,
        resolved_icon_tilted,
        resolved_icon_highlight_color_token,
    ) = _resolve_icon(
        override.get("icon"),
        base_style.icon,
        icons,
        fallback_scale=base_style.icon_scale,
        fallback_tilted=base_style.icon_tilted,
        fallback_highlight_color=base_style.icon_highlight_color,
    )
    configured_icon_font_path = (
        override.get("icon_font_path") if isinstance(override.get("icon_font_path"), str) else None
    )
    scale_value = override.get("scale")
    configured_icon_scale = float(scale_value) if isinstance(scale_value, (int, float)) else None
    configured_icon_tilted = _as_bool(override.get("tilted"), base_style.icon_tilted) if "tilted" in override else None
    configured_icon_highlight_color_token = override.get("highlight_colour")
    if not isinstance(configured_icon_highlight_color_token, str) or not configured_icon_highlight_color_token.strip():
        configured_icon_highlight_color_token = None

    # Resolve highlight color through palette if it's a token, otherwise treat as hex
    highlight_token = (
        configured_icon_highlight_color_token
        if configured_icon_highlight_color_token is not None
        else resolved_icon_highlight_color_token
    )
    resolved_icon_highlight_color: str | None
    if highlight_token:
        fallback_highlight = base_style.icon_highlight_color or "#FFFFFF"
        resolved_icon_highlight_color = _resolve_color(highlight_token, fallback_highlight, palette)
    else:
        resolved_icon_highlight_color = base_style.icon_highlight_color

    configured_flag_keys = _parse_flag_keys(override.get("flags")) if "flags" in override else set(base_style.flag_keys)

    ordered_flags = _ordered_flag_specs(configured_flag_keys, flag_definitions)

    return ProductImageStyle(
        width=_as_int(override.get("width"), base_style.width),
        height=_as_int(override.get("height"), base_style.height),
        background_color=_resolve_color(override.get("background_colour"), base_style.background_color, palette),
        text_color=_resolve_color(override.get("text_colour"), base_style.text_color, palette),
        accent_color=_resolve_color(override.get("accent_colour"), base_style.accent_color, palette),
        title_font_size=_as_int(override.get("title_font_size"), base_style.title_font_size),
        subtitle_font_size=_as_int(override.get("subtitle_font_size"), base_style.subtitle_font_size),
        icon=resolved_icon,
        icon_font_path=configured_icon_font_path or resolved_icon_font_path or base_style.icon_font_path,
        icon_scale=configured_icon_scale if configured_icon_scale is not None else resolved_icon_scale,
        icon_tilted=configured_icon_tilted if configured_icon_tilted is not None else resolved_icon_tilted,
        icon_highlight_color=resolved_icon_highlight_color,
        flag_keys=tuple(sorted(configured_flag_keys)),
        flags=ordered_flags,
    )


def _get_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for font_name in ("DejaVuSans-Bold.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(font_name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _initials_from_name(name: str) -> str:
    raw_tokens = [token for token in re.split(r"[^A-Za-z0-9']+", name.upper()) if token]
    tokens = [token.replace("'", "") for token in raw_tokens if token.replace("'", "")]
    if not tokens:
        return "?"
    if len(tokens) == 1:
        return tokens[0][:2]
    return "".join(token[0] for token in tokens[:3])


def _fit_initials_font(
    draw: ImageDraw.ImageDraw,
    initials: str,
    style: ProductImageStyle,
    max_width: int,
    max_height: int,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    start_size = max(style.title_font_size, int(max_height * 0.7))
    for size in range(start_size, 12, -4):
        font = _get_font(size)
        bbox = draw.textbbox((0, 0), initials, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width <= max_width and text_height <= max_height:
            return font
    return _get_font(12)


def _fit_icon_font(
    draw: ImageDraw.ImageDraw,
    icon_glyph: str,
    style: ProductImageStyle,
    max_width: int,
    max_height: int,
) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    start_size = max(style.title_font_size, int(max_height * 0.95))
    for size in range(start_size, 12, -4):
        font = _get_icon_font(style.icon_font_path, size=size)
        bbox = draw.textbbox((0, 0), icon_glyph, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        if text_width <= max_width and text_height <= max_height:
            return font
    return _get_icon_font(style.icon_font_path, size=12)


def _resolve_icon_glyph(raw_icon: str | None) -> str | None:
    if raw_icon is None:
        return None
    icon = raw_icon.strip()
    if not icon:
        return None
    if len(icon) == 1:
        return icon

    normalized = icon.upper()
    if normalized.startswith("U+"):
        normalized = normalized[2:]
    if normalized.startswith(("\\U", "\\u")):
        normalized = normalized[2:]

    if re.fullmatch(r"[0-9A-F]{4,6}", normalized):
        return chr(int(normalized, 16))

    return icon


def _get_icon_font(icon_font_path: str | None, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont | None:
    if not icon_font_path or not icon_font_path.strip():
        msg = "icon_font_path must be set when icon is configured"
        raise ValueError(msg)

    candidate = Path(icon_font_path.strip())
    if not candidate.is_absolute():
        candidate = Path(__file__).parent / candidate
    if not candidate.exists():
        msg = f"Icon font file not found: {candidate}"
        raise FileNotFoundError(msg)

    try:
        return ImageFont.truetype(str(candidate), size=size)
    except OSError:
        msg = f"Unable to load icon font file: {candidate}"
        raise ValueError(msg) from None


def _draw_top_right_flags(
    image: Image.Image,
    draw: ImageDraw.ImageDraw,
    style: ProductImageStyle,
    flags: list[tuple[str, str]],
) -> None:
    if not flags:
        return

    font_size = max(14, int(min(style.width, style.height) * 0.105))
    flag_font = _get_font(font_size)

    pad_x = max(10, int(min(style.width, style.height) * 0.018))
    left_pad_x = max(pad_x + 1, round(pad_x * 1.5))
    pad_y = max(6, int(min(style.width, style.height) * 0.012))
    bottom_pad_y = pad_y * 2
    radius = max(8, int(min(style.width, style.height) * 0.035))
    border_width = 3
    overdraw = border_width
    reveal_margin = max(6, int(min(style.width, style.height) * 0.012))
    if len(flags) > 1:
        reveal_margin *= 2

    flag_layer = Image.new("RGBA", (style.width, style.height), (0, 0, 0, 0))
    flag_draw = ImageDraw.Draw(flag_layer)

    # Keep label text inside safe margins even if flags extend to canvas edges.
    safe_left = int(style.width * SAFE_MARGIN_SIDE_PCT)
    flag_safe_right_pct = 0.02
    flag_safe_top_pct = max(0.0, SAFE_MARGIN_TOP_PCT - 0.02)
    safe_right = style.width - int(style.width * flag_safe_right_pct)
    safe_top = int(style.height * flag_safe_top_pct)
    safe_bottom = int(style.height * (1.0 - SAFE_MARGIN_BOTTOM_CLEAR_PCT))
    top = -overdraw
    # Keep all flag text aligned to the same baseline row within safe area.
    current_right = style.width - 1 + overdraw
    layout: list[dict[str, Any]] = []

    text_metrics: list[tuple[str, str, tuple[int, int, int, int], int, int]] = []
    for text, fill_color in flags:
        text_bbox = draw.textbbox((0, 0), text, font=flag_font)
        text_width = text_bbox[2] - text_bbox[0]
        text_height = text_bbox[3] - text_bbox[1]
        text_metrics.append((text, fill_color, text_bbox, text_width, text_height))

    row_text_height = max(text_height for _, _, _, _, text_height in text_metrics)
    row_text_bottom = min(safe_bottom, safe_top + row_text_height)

    for text, fill_color, text_bbox, text_width, _text_height in text_metrics:
        flag_width = text_width + pad_x + left_pad_x

        # Anchor text rightward but keep it inside safe margins.
        desired_text_left = current_right - pad_x - text_width
        min_text_left = safe_left
        max_text_left = safe_right - text_width
        text_left = max(min_text_left, min(desired_text_left, max_text_left))

        # Pin each glyph's text-bbox bottom to one shared baseline row.
        text_x = text_left - text_bbox[0]
        text_y = row_text_bottom - text_bbox[3]

        # Ensure box encloses text with internal margins while still extending off-canvas right.
        right = style.width - 1 + overdraw
        left = min(current_right - flag_width + 1, text_left - left_pad_x)
        left = max(0, left)
        bottom = min(style.height - 1, row_text_bottom + bottom_pad_y - 1)

        layout.append(
            {
                "left": left,
                "right": right,
                "bottom": bottom,
                "top": top,
                "radius": radius,
                "border_width": border_width,
                "text": text,
                "text_x": text_x,
                "text_y": text_y,
                "fill_color": fill_color,
            },
        )

        # Next flag sits behind this one from right to left; larger reveal means wider spacing.
        current_right = left - reveal_margin - 1

    # Draw back-to-front so rightmost flags appear in front.
    for spec in reversed(layout):
        _draw_single_flag_spec(
            flag_draw=flag_draw,
            spec=spec,
            flag_font=flag_font,
        )

    image.paste(flag_layer.convert("RGB"), (0, 0), flag_layer)


def _draw_single_flag_spec(
    flag_draw: ImageDraw.ImageDraw,
    spec: dict[str, Any],
    flag_font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> None:
    left = int(spec["left"])
    right = int(spec["right"])
    bottom = int(spec["bottom"])
    top = int(spec["top"])
    radius = int(spec["radius"])
    border_width = int(spec["border_width"])
    fill_color = str(spec["fill_color"])

    # Rectangle body with only bottom-left corner rounded.
    flag_draw.rectangle([(left + radius, top), (right, bottom)], fill=fill_color)
    flag_draw.rectangle([(left, top), (left + radius - 1, bottom - radius)], fill=fill_color)
    flag_draw.pieslice(
        [(left, bottom - (radius * 2) + 1), (left + (radius * 2) - 1, bottom)],
        start=90,
        end=180,
        fill=fill_color,
    )

    # White border around visible edges.
    flag_draw.line([(left, top), (right, top)], fill="#ffffff", width=border_width)
    flag_draw.line([(right, top), (right, bottom)], fill="#ffffff", width=border_width)
    flag_draw.line([(left + radius, bottom), (right, bottom)], fill="#ffffff", width=border_width)
    flag_draw.line([(left, top), (left, bottom - radius)], fill="#ffffff", width=border_width)
    flag_draw.arc(
        [(left, bottom - (radius * 2) + 1), (left + (radius * 2) - 1, bottom)],
        start=90,
        end=180,
        fill="#ffffff",
        width=border_width,
    )

    flag_draw.text(
        (float(spec["text_x"]), float(spec["text_y"])),
        str(spec["text"]),
        fill="#ffffff",
        font=flag_font,
    )


def _draw_tilted_icon(
    image: Image.Image,
    text_color: str,
    params: TiltedIconRenderParams,
) -> Image.Image:
    # Render glyph to a transparent layer, rotate 45 degrees clockwise, then composite.
    pad = params.text_stroke_width + 2
    glyph_w = int(params.text_width + (pad * 2))
    glyph_h = int(params.text_height + (pad * 2))
    glyph_layer = Image.new("RGBA", (glyph_w, glyph_h), (0, 0, 0, 0))
    glyph_draw = ImageDraw.Draw(glyph_layer)
    glyph_draw.text(
        (pad - params.bbox[0], pad - params.bbox[1]),
        params.content,
        fill=text_color,
        font=params.font,
        stroke_width=params.text_stroke_width,
        stroke_fill=params.text_stroke_color,
    )

    rotated_glyph = glyph_layer.rotate(-45, expand=True, resample=Image.Resampling.BICUBIC)
    center_x = params.text_x + (params.text_width / 2)
    center_y = params.text_y + (params.text_height / 2)
    paste_x = round(center_x - (rotated_glyph.width / 2))
    paste_y = round(center_y - (rotated_glyph.height / 2))

    image_rgba = image.convert("RGBA")
    image_rgba.alpha_composite(rotated_glyph, (paste_x, paste_y))
    return image_rgba.convert("RGB")


def _draw_icon_highlight(  # noqa: PLR0913
    image: Image.Image,
    content: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    text_x: int,
    text_y: int,
    text_width: int,
    text_height: int,
    bbox: tuple[int, int, int, int],
    highlight_color: str,
    text_stroke_width: int,
    text_stroke_color: str = "#000000",
) -> Image.Image:
    """Render a full-width highlight stripe centered in the tile's vertical safe zone."""
    image_rgba = image.convert("RGBA")
    draw = ImageDraw.Draw(image_rgba)

    image_height = image_rgba.height
    border_thickness = 3

    # Safe zone is shared with content layout and driven by top/bottom margin constants.
    safe_zone_top = int(image_height * SAFE_MARGIN_TOP_PCT)
    safe_zone_bottom = int(image_height * (1.0 - SAFE_MARGIN_BOTTOM_CLEAR_PCT))
    safe_zone_center = (safe_zone_top + safe_zone_bottom) // 2

    # Total stripe height includes both 3px borders and the center fill.
    total_stripe_height = max((border_thickness * 2) + 1, round(image_height * 0.18))
    stripe_y_start = safe_zone_center - (total_stripe_height // 2)
    stripe_y_end = stripe_y_start + total_stripe_height

    # Parse highlight color hex to RGB tuple
    highlight_rgb = tuple(int(highlight_color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))

    # Calculate darkened border color (60% darkened)
    border_rgb = tuple(max(0, int(c * 0.4)) for c in highlight_rgb)

    # Clamp vertical coordinates to image bounds before drawing.
    top_border_start = max(0, stripe_y_start)
    top_border_end = min(image_height - 1, stripe_y_start + border_thickness - 1)
    stripe_start = max(0, stripe_y_start + border_thickness)
    stripe_end = min(image_height - 1, stripe_y_end - border_thickness - 1)
    bottom_border_start = max(0, stripe_y_end - border_thickness)
    bottom_border_end = min(image_height - 1, stripe_y_end - 1)

    # Draw 3px top border
    if top_border_start <= top_border_end:
        draw.rectangle(
            [(0, top_border_start), (image_rgba.width - 1, top_border_end)],
            fill=border_rgb + (255,),
        )

    # Draw main stripe
    if stripe_start <= stripe_end:
        draw.rectangle(
            [(0, stripe_start), (image_rgba.width - 1, stripe_end)],
            fill=highlight_rgb + (255,),
        )

    # Draw 3px bottom border
    if bottom_border_start <= bottom_border_end:
        draw.rectangle(
            [(0, bottom_border_start), (image_rgba.width - 1, bottom_border_end)],
            fill=border_rgb + (255,),
        )

    return image_rgba.convert("RGB")


def _render_image_bytes(target: ProductImageTarget, style: ProductImageStyle) -> bytes:
    image = Image.new("RGB", (style.width, style.height), style.background_color)
    draw = ImageDraw.Draw(image)

    # Shared safe zone margins used for all content layout.
    safe_left = int(style.width * SAFE_MARGIN_SIDE_PCT)
    safe_right = style.width - safe_left
    safe_top = int(style.height * SAFE_MARGIN_TOP_PCT)
    safe_bottom = int(style.height * (1.0 - SAFE_MARGIN_BOTTOM_CLEAR_PCT))

    icon_glyph = _resolve_icon_glyph(style.icon)
    initials = _initials_from_name(target.name)
    content = icon_glyph or initials

    max_width = max(1, safe_right - safe_left)
    max_height = max(1, safe_bottom - safe_top)
    initials_font = _fit_initials_font(draw, content, style, max_width=max_width, max_height=max_height)

    if icon_glyph:
        initials_font = _fit_icon_font(
            draw,
            icon_glyph,
            style,
            max_width=max_width,
            max_height=max_height,
        )
        if style.icon_scale < 1.0:
            fitted_size = getattr(initials_font, "size", style.title_font_size)
            scaled_size = max(12, int(fitted_size * style.icon_scale))
            initials_font = _get_icon_font(style.icon_font_path, size=scaled_size)

    bbox = draw.textbbox((0, 0), content, font=initials_font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    min_x = safe_left - bbox[0]
    max_x = safe_right - bbox[2]
    min_y = safe_top - bbox[1]
    max_y = safe_bottom - bbox[3]

    safe_center_x = (safe_left + safe_right) / 2
    safe_center_y = (safe_top + safe_bottom) / 2
    text_x = safe_center_x - ((bbox[0] + bbox[2]) / 2)
    text_y = safe_center_y - ((bbox[1] + bbox[3]) / 2)
    text_x = min(max(text_x, min_x), max_x)
    text_y = min(max(text_y, min_y), max_y)

    text_stroke_color = _darken_color(style.text_color, amount=0.6)
    text_stroke_width = max(1, min(4, int(getattr(initials_font, "size", style.title_font_size) * 0.03)))

    # Apply highlight stripe before drawing text/icon so it renders behind content
    if style.icon_highlight_color:
        image = _draw_icon_highlight(
            image,
            content,
            initials_font,
            text_x,
            text_y,
            text_width,
            text_height,
            bbox,
            style.icon_highlight_color,
            text_stroke_width,
            text_stroke_color,
        )
        draw = ImageDraw.Draw(image)  # Refresh draw object after modifying image

    if icon_glyph and style.icon_tilted:
        tilted_params = TiltedIconRenderParams(
            content=content,
            font=initials_font,
            bbox=bbox,
            text_width=text_width,
            text_height=text_height,
            text_x=text_x,
            text_y=text_y,
            text_stroke_width=text_stroke_width,
            text_stroke_color=text_stroke_color,
        )
        image = _draw_tilted_icon(
            image,
            style.text_color,
            tilted_params,
        )
    else:
        draw.text(
            (text_x, text_y),
            content,
            fill=style.text_color,
            font=initials_font,
            stroke_width=text_stroke_width,
            stroke_fill=text_stroke_color,
        )

    # Flags render last so they sit in front of text/icons.
    flags_to_render: list[tuple[str, str]] = []
    if target.has_variants:
        flags_to_render.append(("+", "#2e7d32"))
    flags_to_render.extend(style.flags)
    _draw_top_right_flags(image, draw, style, flags_to_render)

    as_bytes = BytesIO()
    image.save(as_bytes, format="PNG")
    return as_bytes.getvalue()


def _resolve_output_dir(raw_config: dict[str, Any]) -> Path:
    value = raw_config.get("output_dir")
    if isinstance(value, str) and value.strip():
        configured = Path(value.strip())
        return configured if configured.is_absolute() else Path(__file__).parent / configured
    return OUTPUT_DIR / "product-images"


def _seed_new_product_ids(products_file: Path, products: list[ProductImageTarget]) -> int:
    config_doc = _load_products_config_document(products_file)
    raw_overrides = config_doc.get("product_id_overrides")

    existing_overrides = CommentedMap()
    if isinstance(raw_overrides, dict):
        for key, value in raw_overrides.items():
            existing_overrides[str(key)] = value

    overrides = CommentedMap()

    new_count = 0
    products_by_name = sorted(products, key=lambda product: product.name.lower())
    for product in products_by_name:
        existing_value = existing_overrides.pop(product.item_id, None)
        if isinstance(existing_value, CommentedMap):
            overrides[product.item_id] = existing_value
        elif isinstance(existing_value, dict):
            overrides[product.item_id] = CommentedMap(existing_value)
        else:
            overrides[product.item_id] = CommentedMap()
            new_count += 1

        overrides.yaml_add_eol_comment(product.name, key=product.item_id)

    for item_id in sorted(existing_overrides):
        value = existing_overrides[item_id]
        if isinstance(value, CommentedMap):
            overrides[item_id] = value
            continue
        if isinstance(value, dict):
            overrides[item_id] = CommentedMap(value)
            continue
        overrides[item_id] = CommentedMap()

    config_doc["product_id_overrides"] = overrides

    with products_file.open("w") as file_handle:
        YAML_RT.dump(config_doc, file_handle)

    return new_count


def run_product_image_sync(
    products_file: Path,
    write: bool,
) -> ProductImageSyncSummary:
    items = get_loyverse_items()
    targets = _build_targets_from_loyverse_items(items)
    new_product_ids_added = _seed_new_product_ids(products_file, targets)

    raw_config = _load_products_config(products_file)
    base_style = _style_from_config(raw_config)

    output_dir = _resolve_output_dir(raw_config)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Load image cache
    cache_file = output_dir / ".image_cache.json"
    image_hashes: dict[str, str] = {}
    if cache_file.exists():
        try:
            loaded_cache = json.loads(cache_file.read_text())
            if isinstance(loaded_cache, dict):
                image_hashes = {
                    key: value for key, value in loaded_cache.items() if isinstance(key, str) and isinstance(value, str)
                }
        except (json.JSONDecodeError, OSError):
            image_hashes = {}

    image_paths_by_item_id: dict[str, Path] = {}
    image_hashes_by_item_id: dict[str, str] = {}
    for target in targets:
        style = _style_for_product_id(raw_config, base_style, target.item_id)
        image_bytes = _render_image_bytes(target, style)
        image_path = output_dir / f"{target.item_id}.png"
        image_path.write_bytes(image_bytes)
        image_paths_by_item_id[target.item_id] = image_path

        # Compute and track hash
        image_hash = hashlib.sha256(image_bytes).hexdigest()
        image_hashes_by_item_id[target.item_id] = image_hash

    if not write:
        return ProductImageSyncSummary(
            built_images=len(image_paths_by_item_id),
            uploaded_images=0,
            upload_failures=0,
            new_product_ids_added=new_product_ids_added,
        )

    uploaded_images = 0
    upload_failures = 0

    for item_id, image_path in image_paths_by_item_id.items():
        new_hash = image_hashes_by_item_id[item_id]
        old_hash = image_hashes.get(item_id)

        # Skip if hash hasn't changed
        if new_hash == old_hash:
            click.echo(click.style(f"[UNCHANGED] {item_id}", dim=True))
            continue

        image_bytes = image_path.read_bytes()
        ok, message = upload_item_image(item_id, image_bytes)
        if ok:
            uploaded_images += 1
            image_hashes[item_id] = new_hash
            click.echo(click.style(f"[UPLOADED] {item_id}", fg="green"))
            continue

        upload_failures += 1
        click.echo(click.style(f"[FAILED] {item_id} -> {message}", fg="red"))

    # Save updated cache
    cache_file.write_text(json.dumps(image_hashes, indent=2))

    return ProductImageSyncSummary(
        built_images=len(image_paths_by_item_id),
        uploaded_images=uploaded_images,
        upload_failures=upload_failures,
        new_product_ids_added=new_product_ids_added,
    )


def print_product_image_summary(summary: ProductImageSyncSummary, write: bool) -> None:
    click.echo("")
    click.echo(click.style("Product image summary", bold=True))
    click.echo(f"Built images: {summary.built_images}")
    click.echo(f"New product IDs added to products.yaml: {summary.new_product_ids_added}")
    if not write:
        click.echo(click.style("Dry-run complete. Use --write to upload images to Loyverse.", fg="cyan"))
        return

    click.echo(f"Uploaded images: {summary.uploaded_images}")
    click.echo(f"Upload failures: {summary.upload_failures}")
