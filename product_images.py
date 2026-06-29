import hashlib
import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import click
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageFont
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

DEFAULT_HIGHLIGHT_COLOR = "#FFFFFF"
FLAG_VARIANT_TEXT = "+"
FLAG_VARIANT_COLOR = "#2e7d32"
FLAG_RIGHT_MARGIN_REDUCTION_PCT = 0.02
FLAG_TOP_MARGIN_REDUCTION_PCT = 0.02
FLAG_LEFT_PAD_MULTIPLIER = 1.5
FLAG_MULTI_REVEAL_MULTIPLIER = 2
HIGHLIGHT_STRIPE_HEIGHT_PCT = 0.12
OUTER_BORDER_WIDTH = 12  # Outer border stroke width (text color based)
INNER_BORDER_WIDTH = 6  # Inner border stroke width (background color based)


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
    text_color: str
    background_color: str
    text_is_lighter: bool
    image_width: int
    image_height: int


@dataclass(frozen=True)
class StyleAssets:
    palette: dict[str, str]
    icons: dict[str, dict[str, str | float | bool | None]]
    flag_definitions: dict[str, tuple[str, str]]


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


def _relative_luminance(color: str) -> float:
    """
    Calculate the relative luminance of a color according to WCAG 2.0.

    Formula from: https://www.w3.org/TR/WCAG20/#relativeluminancedef
    """
    red, green, blue = ImageColor.getrgb(_safe_color(color, "#000000"))

    # Convert 8-bit RGB to 0-1 range
    r_srgb = red / 255.0
    g_srgb = green / 255.0
    b_srgb = blue / 255.0

    # Apply gamma correction to get linear RGB
    def _linearize(channel: float) -> float:
        if channel <= 0.03928:
            return channel / 12.92
        return ((channel + 0.055) / 1.055) ** 2.4

    r_linear = _linearize(r_srgb)
    g_linear = _linearize(g_srgb)
    b_linear = _linearize(b_srgb)

    # Calculate relative luminance
    return 0.2126 * r_linear + 0.7152 * g_linear + 0.0722 * b_linear


def _should_darken_glow(border_color: str, background_color: str, text_color: str) -> bool:
    """
    Determine if glow should darken (True) or lighten (False).

    The glow should enhance the border's contrast with the background:
    - Dark border on light background → black glow (shadow)
    - Light border on dark background → white glow (halo)

    When border equals background (high contrast case), base decision on text.
    """
    border_lum = _relative_luminance(border_color)
    bg_lum = _relative_luminance(background_color)

    # If border equals background (within small tolerance), use text color instead
    if abs(border_lum - bg_lum) < 0.001:
        text_lum = _relative_luminance(text_color)
        # If text is darker than background, darken the glow (create shadow)
        # If text is lighter than background, lighten the glow (create halo)
        return text_lum < bg_lum

    # Otherwise: if border is lighter than background, darken (black glow)
    # If border is darker than background, lighten (white glow/screen blend)
    return border_lum > bg_lum


def _contrast_ratio(color1: str, color2: str) -> float:
    """Calculate WCAG 2.0 contrast ratio between two colors."""
    lum1 = _relative_luminance(color1)
    lum2 = _relative_luminance(color2)
    lighter = max(lum1, lum2)
    darker = min(lum1, lum2)
    return (lighter + 0.05) / (darker + 0.05)


def _adjust_color_for_contrast(
    base_color: str,
    reference_color: str,
    target_ratio: float = 7.0,
    darken: bool = False,
) -> str:
    """
    Adjust base_color to achieve target contrast ratio with reference_color.

    If the colors already meet or exceed the target ratio, return base_color unchanged.
    Otherwise, calculate the exact luminance needed and scale the color mathematically.

    Note: If the target ratio requires a luminance outside the sRGB gamut (< 0 or > 1),
    the function returns the best achievable result within gamut, which may not meet
    the target ratio. This commonly occurs when trying to achieve high contrast ratios
    with already-dark or already-bright colors.

    Args:
        base_color: Color to adjust
        reference_color: Color to contrast against
        target_ratio: Target WCAG contrast ratio (default 7.0)
        darken: True to darken, False to lighten (direction to adjust)

    Returns:
        Adjusted hex color string
    """
    # Check if we already meet the target
    current_ratio = _contrast_ratio(base_color, reference_color)
    if current_ratio >= target_ratio:
        return base_color

    base_lum = _relative_luminance(base_color)
    ref_lum = _relative_luminance(reference_color)

    # Calculate target luminance for desired contrast ratio
    if darken:
        # Making base_color darker, so it becomes the darker color in the ratio
        # ratio = (ref_lum + 0.05) / (target_lum + 0.05)
        # target_lum = (ref_lum + 0.05) / ratio - 0.05
        target_lum = (ref_lum + 0.05) / target_ratio - 0.05
        target_lum = max(0.0, min(base_lum, target_lum))  # Can't go lighter when darkening
    else:
        # Making base_color lighter, so it becomes the lighter color in the ratio
        # ratio = (target_lum + 0.05) / (ref_lum + 0.05)
        # target_lum = ratio * (ref_lum + 0.05) - 0.05
        target_lum = target_ratio * (ref_lum + 0.05) - 0.05
        target_lum = max(base_lum, min(1.0, target_lum))  # Can't go darker when lightening

    # Convert to linear RGB, scale, convert back
    r, g, b = ImageColor.getrgb(base_color)

    def _to_linear(channel: int) -> float:
        srgb = channel / 255.0
        if srgb <= 0.03928:
            return srgb / 12.92
        return ((srgb + 0.055) / 1.055) ** 2.4

    def _from_linear(linear: float) -> int:
        linear = max(0.0, min(1.0, linear))
        srgb = linear * 12.92 if linear <= 0.0031308 else 1.055 * (linear ** (1 / 2.4)) - 0.055
        return int(max(0, min(255, srgb * 255)))

    if base_lum < 0.0001:
        # Pure black, can't scale, so just move toward target
        if not darken:
            # Lightening from black - use a gray that achieves the target
            scaled_r = scaled_g = scaled_b = _from_linear(min(1.0, target_lum / 0.2126))
        else:
            return base_color  # Can't darken pure black
    else:
        scale = target_lum / base_lum

        # Check if scaling would push any channel out of gamut
        r_lin = _to_linear(r)
        g_lin = _to_linear(g)
        b_lin = _to_linear(b)

        scaled_r_lin = r_lin * scale
        scaled_g_lin = g_lin * scale
        scaled_b_lin = b_lin * scale

        # If any channel exceeds gamut, we can't achieve target via uniform scaling
        # Return the color clamped to gamut (best effort)
        if max(scaled_r_lin, scaled_g_lin, scaled_b_lin) > 1.0 or min(scaled_r_lin, scaled_g_lin, scaled_b_lin) < 0.0:
            # Clamp channels and accept reduced contrast
            scaled_r = _from_linear(scaled_r_lin)
            scaled_g = _from_linear(scaled_g_lin)
            scaled_b = _from_linear(scaled_b_lin)
        else:
            scaled_r = _from_linear(scaled_r_lin)
            scaled_g = _from_linear(scaled_g_lin)
            scaled_b = _from_linear(scaled_b_lin)

    return f"#{scaled_r:02x}{scaled_g:02x}{scaled_b:02x}"


def _create_glow_layer(  # noqa: PLR0913
    content: str,
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    text_x: float,
    text_y: float,
    bbox: tuple[float, float, float, float],
    image_width: int,
    image_height: int,
    darken: bool,
    blur_radius: int = 10,
    opacity: float = 1.0,
) -> Image.Image:
    """
    Create a blurred glow layer that darkens or lightens areas beneath text/icons.

    Args:
        content: Text or icon glyph to render
        font: Font to use
        text_x, text_y: Position of text
        bbox: Bounding box of text (not used in non-rotated case but kept for API consistency)
        image_width, image_height: Dimensions of final image
        darken: True to darken (use black), False to lighten (use white)
        blur_radius: Gaussian blur radius in pixels
        opacity: Opacity of the glow effect (0.0-1.0)

    Returns:
        RGBA image with blurred glow
    """
    # Use semi-transparent black (darken) or white (lighten)
    alpha = int(opacity * 255)
    glow_color = (0, 0, 0, alpha) if darken else (255, 255, 255, alpha)

    # Create glow on full canvas
    glow_layer = Image.new("RGBA", (image_width, image_height), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_draw.text(
        (text_x, text_y),
        content,
        fill=glow_color,
        font=font,
    )

    # Apply Gaussian blur to create glow effect
    return glow_layer.filter(ImageFilter.GaussianBlur(radius=blur_radius))


def _composite_glow_layer(base_image: Image.Image, glow_layer: Image.Image, darken: bool) -> Image.Image:
    """
    Composite a glow layer onto base image using appropriate blend mode.

    For lightening (darken=False): uses screen blend mode to only lighten
    For darkening (darken=True): uses multiply blend mode to only darken

    Args:
        base_image: Base RGB image
        glow_layer: RGBA glow layer with alpha channel
        darken: True for darken/multiply, False for lighten/screen

    Returns:
        Composited RGB image
    """
    base_rgba = base_image.convert("RGBA")

    # Split glow layer into RGB and alpha
    glow_rgb = glow_layer.convert("RGB")
    glow_alpha = glow_layer.split()[3]  # Alpha channel

    # Convert to numpy-like operations using pixel access
    base_pixels = base_rgba.load()
    glow_rgb_pixels = glow_rgb.load()
    glow_alpha_pixels = glow_alpha.load()

    result = base_rgba.copy()
    result_pixels = result.load()

    width, height = base_rgba.size

    for y in range(height):
        for x in range(width):
            alpha = glow_alpha_pixels[x, y] / 255.0
            if alpha > 0:
                base_r, base_g, base_b, base_a = base_pixels[x, y]
                glow_r, glow_g, glow_b = glow_rgb_pixels[x, y]

                if darken:
                    # Multiply blend: result = base * (glow / 255)
                    new_r = int(base_r * (glow_r / 255.0))
                    new_g = int(base_g * (glow_g / 255.0))
                    new_b = int(base_b * (glow_b / 255.0))
                else:
                    # Screen blend: result = 1 - (1 - base) * (1 - glow)
                    new_r = int(255 - (255 - base_r) * (255 - glow_r) / 255.0)
                    new_g = int(255 - (255 - base_g) * (255 - glow_g) / 255.0)
                    new_b = int(255 - (255 - base_b) * (255 - glow_b) / 255.0)

                # Apply alpha blending between original and blended
                final_r = int(base_r + (new_r - base_r) * alpha)
                final_g = int(base_g + (new_g - base_g) * alpha)
                final_b = int(base_b + (new_b - base_b) * alpha)

                result_pixels[x, y] = (final_r, final_g, final_b, base_a)

    return result.convert("RGB")


def _calculate_optimal_border_color(text_color: str, background_color: str) -> str:  # noqa: C901
    """
    Calculate optimal border color to maximize contrast with both text and background.

    Tries two approaches:
    1. Scale background to achieve 7:1 with text
    2. Scale text to achieve 7:1 with background

    Returns whichever option provides better minimum contrast with both colors.
    If text and background already have ≥7:1 contrast, returns background unchanged.
    """
    target_ratio = 7.0

    # Get luminances
    text_lum = _relative_luminance(text_color)
    bg_lum = _relative_luminance(background_color)

    # Calculate current contrast ratio between text and background
    lighter = max(text_lum, bg_lum)
    darker = min(text_lum, bg_lum)
    current_ratio = (lighter + 0.05) / (darker + 0.05)

    # If contrast is already sufficient, use background as-is
    if current_ratio >= target_ratio:
        return background_color

    def _scale_color_to_luminance(color: str, source_lum: float, target_lum: float) -> str | None:
        """Scale a color's luminance. Returns None if not possible."""
        if source_lum < 0.0001:
            return None

        target_lum = max(0.0, min(1.0, target_lum))
        scale = target_lum / source_lum

        r, g, b = ImageColor.getrgb(_safe_color(color, "#000000"))

        def _to_linear(channel: int) -> float:
            srgb = channel / 255.0
            if srgb <= 0.03928:
                return srgb / 12.92
            return ((srgb + 0.055) / 1.055) ** 2.4

        def _from_linear(linear: float) -> int:
            linear = max(0.0, min(1.0, linear))
            srgb = linear * 12.92 if linear <= 0.0031308 else 1.055 * (linear ** (1 / 2.4)) - 0.055
            return int(max(0, min(255, srgb * 255)))

        # Scale in linear space
        scaled_r = _from_linear(_to_linear(r) * scale)
        scaled_g = _from_linear(_to_linear(g) * scale)
        scaled_b = _from_linear(_to_linear(b) * scale)

        return f"#{scaled_r:02x}{scaled_g:02x}{scaled_b:02x}"

    def _contrast_ratio(lum1: float, lum2: float) -> float:
        """Calculate contrast ratio between two luminances."""
        lighter = max(lum1, lum2)
        darker = min(lum1, lum2)
        return (lighter + 0.05) / (darker + 0.05)

    # Option 1: Scale background to achieve 7:1 with text
    if text_lum > bg_lum:
        target_lum_1 = (text_lum + 0.05) / target_ratio - 0.05
    else:
        target_lum_1 = target_ratio * (text_lum + 0.05) - 0.05

    option1 = _scale_color_to_luminance(background_color, bg_lum, target_lum_1)

    # Option 2: Scale text to achieve 7:1 with background
    target_lum_2 = (bg_lum + 0.05) / target_ratio - 0.05 if bg_lum > text_lum else target_ratio * (bg_lum + 0.05) - 0.05

    option2 = _scale_color_to_luminance(text_color, text_lum, target_lum_2)

    # Evaluate both options and pick the one with better minimum contrast
    candidates = []

    if option1:
        option1_lum = _relative_luminance(option1)
        min_contrast_1 = min(
            _contrast_ratio(option1_lum, text_lum),
            _contrast_ratio(option1_lum, bg_lum),
        )
        candidates.append((option1, min_contrast_1))

    if option2:
        option2_lum = _relative_luminance(option2)
        min_contrast_2 = min(
            _contrast_ratio(option2_lum, text_lum),
            _contrast_ratio(option2_lum, bg_lum),
        )
        candidates.append((option2, min_contrast_2))

    # Return the option with the best minimum contrast
    if candidates:
        return max(candidates, key=lambda x: x[1])[0]

    # Fallback: use background color
    return background_color


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


def _parse_style_assets(raw_config: dict[str, Any]) -> StyleAssets:
    palette = _parse_palette(raw_config)
    icons = _parse_icons(raw_config)
    flag_definitions = _parse_flags(raw_config, palette)
    return StyleAssets(
        palette=palette,
        icons=icons,
        flag_definitions=flag_definitions,
    )


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
    style_assets = _parse_style_assets(raw_config)
    palette = style_assets.palette
    icons = style_assets.icons
    flag_definitions = style_assets.flag_definitions
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
    resolved_icon_highlight_color = (
        _resolve_color(highlight_token, DEFAULT_HIGHLIGHT_COLOR, palette) if highlight_token else None
    )

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


def _style_for_product_id(
    raw_config: dict[str, Any],
    base_style: ProductImageStyle,
    item_id: str,
    *,
    style_assets: StyleAssets | None = None,
) -> ProductImageStyle:
    overrides = raw_config.get("product_id_overrides", {})
    if not isinstance(overrides, dict):
        return base_style
    override = overrides.get(item_id, {})
    if not isinstance(override, dict):
        return base_style
    resolved_style_assets = style_assets if style_assets is not None else _parse_style_assets(raw_config)
    palette = resolved_style_assets.palette
    icons = resolved_style_assets.icons
    flag_definitions = resolved_style_assets.flag_definitions
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
        fallback_highlight = base_style.icon_highlight_color or DEFAULT_HIGHLIGHT_COLOR
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

    number_token = next((token for token in tokens if any(char.isdigit() for char in token)), None)
    if number_token is not None:
        if number_token == tokens[0]:
            return number_token
        return f"{tokens[0][0]}{number_token}"

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


def _safe_horizontal_bounds(
    width: int,
    *,
    left_pct: float = SAFE_MARGIN_SIDE_PCT,
    right_pct: float | None = None,
) -> tuple[int, int]:
    resolved_right_pct = left_pct if right_pct is None else right_pct
    left = int(width * left_pct)
    right = width - int(width * resolved_right_pct)
    return left, right


def _safe_vertical_bounds(
    height: int,
    *,
    top_pct: float = SAFE_MARGIN_TOP_PCT,
    bottom_clear_pct: float = SAFE_MARGIN_BOTTOM_CLEAR_PCT,
) -> tuple[int, int]:
    top = int(height * top_pct)
    bottom = int(height * (1.0 - bottom_clear_pct))
    return top, bottom


def _flag_reference_text_height(
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
    draw: ImageDraw.ImageDraw,
) -> int:
    # Use a shared reference so '+' and 'AF' flags are consistently sized across products.
    af_bbox = draw.textbbox((0, 0), "AF", font=font)
    plus_bbox = draw.textbbox((0, 0), "+", font=font)
    af_height = af_bbox[3] - af_bbox[1]
    plus_height = plus_bbox[3] - plus_bbox[1]
    return max(1, af_height, plus_height)


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
    left_pad_x = max(pad_x + 1, round(pad_x * FLAG_LEFT_PAD_MULTIPLIER))
    pad_y = max(6, int(min(style.width, style.height) * 0.012))
    bottom_pad_y = pad_y * 2
    radius = max(8, int(min(style.width, style.height) * 0.035))
    border_width = 3
    overdraw = border_width
    reveal_margin = max(6, int(min(style.width, style.height) * 0.012))
    if len(flags) > 1:
        reveal_margin *= FLAG_MULTI_REVEAL_MULTIPLIER

    flag_layer = Image.new("RGBA", (style.width, style.height), (0, 0, 0, 0))
    flag_draw = ImageDraw.Draw(flag_layer)

    # Keep label text inside safe margins even if flags extend to canvas edges.
    flag_safe_right_pct = max(0.0, SAFE_MARGIN_SIDE_PCT - FLAG_RIGHT_MARGIN_REDUCTION_PCT)
    safe_left, safe_right = _safe_horizontal_bounds(style.width, right_pct=flag_safe_right_pct)
    flag_safe_top_pct = max(0.0, SAFE_MARGIN_TOP_PCT - FLAG_TOP_MARGIN_REDUCTION_PCT)
    safe_top, safe_bottom = _safe_vertical_bounds(style.height, top_pct=flag_safe_top_pct)
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

    max_glyph_height = max(text_height for _, _, _, _, text_height in text_metrics)
    row_text_height = max(_flag_reference_text_height(flag_font, draw), max_glyph_height)
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
    params: TiltedIconRenderParams,
) -> Image.Image:
    # Double border approach:
    # - Outer border: based on text color, adjusted for 7:1 contrast with background
    # - Inner border: based on background color, adjusted for 7:1 contrast with text
    outer_stroke_color = _adjust_color_for_contrast(
        params.text_color,
        params.background_color,
        target_ratio=7.0,
        darken=not params.text_is_lighter,
    )
    inner_stroke_color = _adjust_color_for_contrast(
        params.background_color,
        params.text_color,
        target_ratio=7.0,
        darken=params.text_is_lighter,
    )

    pad_outer = OUTER_BORDER_WIDTH + 2
    glyph_w = int(params.text_width + (pad_outer * 2))
    glyph_h = int(params.text_height + (pad_outer * 2))

    # Outer layer with adjusted text color stroke
    outer_layer = Image.new("RGBA", (glyph_w, glyph_h), (0, 0, 0, 0))
    outer_draw = ImageDraw.Draw(outer_layer)
    outer_draw.text(
        (pad_outer - params.bbox[0], pad_outer - params.bbox[1]),
        params.content,
        fill=params.text_color,
        font=params.font,
        stroke_width=OUTER_BORDER_WIDTH,
        stroke_fill=outer_stroke_color,
    )

    rotated_outer = outer_layer.rotate(-45, expand=True, resample=Image.Resampling.BICUBIC)

    center_x = params.text_x + (params.text_width / 2)
    center_y = params.text_y + (params.text_height / 2)
    paste_x_outer = round(center_x - (rotated_outer.width / 2))
    paste_y_outer = round(center_y - (rotated_outer.height / 2))

    image_rgba = image.convert("RGBA")
    image_rgba.alpha_composite(rotated_outer, (paste_x_outer, paste_y_outer))

    # Inner layer with adjusted background color stroke
    pad_inner = INNER_BORDER_WIDTH + 2
    glyph_w_inner = int(params.text_width + (pad_inner * 2))
    glyph_h_inner = int(params.text_height + (pad_inner * 2))

    inner_layer = Image.new("RGBA", (glyph_w_inner, glyph_h_inner), (0, 0, 0, 0))
    inner_draw = ImageDraw.Draw(inner_layer)
    inner_draw.text(
        (pad_inner - params.bbox[0], pad_inner - params.bbox[1]),
        params.content,
        fill=params.text_color,
        font=params.font,
        stroke_width=INNER_BORDER_WIDTH,
        stroke_fill=inner_stroke_color,
    )

    rotated_inner = inner_layer.rotate(-45, expand=True, resample=Image.Resampling.BICUBIC)
    paste_x = round(center_x - (rotated_inner.width / 2))
    paste_y = round(center_y - (rotated_inner.height / 2))

    image_rgba.alpha_composite(rotated_inner, (paste_x, paste_y))
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
    safe_zone_top, safe_zone_bottom = _safe_vertical_bounds(image_height)
    safe_zone_center = (safe_zone_top + safe_zone_bottom) // 2

    # Total stripe height includes both 3px borders and the center fill.
    total_stripe_height = max((border_thickness * 2) + 1, round(image_height * HIGHLIGHT_STRIPE_HEIGHT_PCT))
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


def _render_image_bytes(target: ProductImageTarget, style: ProductImageStyle) -> bytes:  # noqa: PLR0915
    image = Image.new("RGB", (style.width, style.height), style.background_color)
    draw = ImageDraw.Draw(image)

    # Shared safe zone margins used for all content layout.
    safe_left, safe_right = _safe_horizontal_bounds(style.width)
    safe_top, safe_bottom = _safe_vertical_bounds(style.height)

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

    # Determine adjustment direction based on which is lighter
    text_lum = _relative_luminance(style.text_color)
    bg_lum = _relative_luminance(style.background_color)
    text_is_lighter = text_lum > bg_lum

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
            INNER_BORDER_WIDTH,
            style.background_color,
        )
        draw = ImageDraw.Draw(image)  # Refresh draw object after modifying image

    if icon_glyph and style.icon_tilted:
        # For tilted icons, glow is handled inside _draw_tilted_icon
        tilted_params = TiltedIconRenderParams(
            content=content,
            font=initials_font,
            bbox=bbox,
            text_width=text_width,
            text_height=text_height,
            text_x=text_x,
            text_y=text_y,
            text_color=style.text_color,
            background_color=style.background_color,
            text_is_lighter=text_is_lighter,
            image_width=style.width,
            image_height=style.height,
        )
        image = _draw_tilted_icon(
            image,
            tilted_params,
        )
        draw = ImageDraw.Draw(image)  # Refresh draw object after tilted icon
    else:
        # Double border approach:
        # - Outer border: based on text color, adjusted for 7:1 contrast with background
        # - Inner border: based on background color, adjusted for 7:1 contrast with text
        outer_stroke_color = _adjust_color_for_contrast(
            style.text_color,
            style.background_color,
            target_ratio=7.0,
            darken=not text_is_lighter,
        )
        inner_stroke_color = _adjust_color_for_contrast(
            style.background_color,
            style.text_color,
            target_ratio=7.0,
            darken=text_is_lighter,
        )

        # Draw outer border first (adjusted text color)
        draw.text(
            (text_x, text_y),
            content,
            fill=style.text_color,
            font=initials_font,
            stroke_width=OUTER_BORDER_WIDTH,
            stroke_fill=outer_stroke_color,
        )

        # Draw inner border on top (adjusted background color)
        draw.text(
            (text_x, text_y),
            content,
            fill=style.text_color,
            font=initials_font,
            stroke_width=INNER_BORDER_WIDTH,
            stroke_fill=inner_stroke_color,
        )

    # Flags render last so they sit in front of text/icons.
    flags_to_render: list[tuple[str, str]] = []
    if target.has_variants:
        flags_to_render.append((FLAG_VARIANT_TEXT, FLAG_VARIANT_COLOR))
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
    style_assets = _parse_style_assets(raw_config)
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
        style = _style_for_product_id(
            raw_config,
            base_style,
            target.item_id,
            style_assets=style_assets,
        )
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
