"""Color palettes and color utility functions for Freerun."""

from __future__ import annotations
import colorsys
from typing import Tuple, List, Dict

RGB = Tuple[float, float, float]
RGBA = Tuple[float, float, float, float]


def hex_to_rgb(hex_str: str) -> RGB:
    """Convert hex string (e.g. '#00f2fe' or '00f2fe') to normalized (r, g, b)."""
    hex_str = hex_str.strip().lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join([c * 2 for c in hex_str])
    if len(hex_str) != 6:
        raise ValueError(f"Invalid hex color: {hex_str}")
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    return (r, g, b)


# Preset palettes
PALETTES: Dict[str, List[RGB]] = {
    "neon": [
        hex_to_rgb("#00f2fe"),  # electric cyan
        hex_to_rgb("#ff0844"),  # vibrant magenta / hot pink
        hex_to_rgb("#7928ca"),  # neon violet
        hex_to_rgb("#ffb100"),  # electric amber
        hex_to_rgb("#00f5a0"),  # mint lime
    ],
    "synthwave": [
        hex_to_rgb("#ff007f"),  # hot magenta
        hex_to_rgb("#9900ff"),  # purple
        hex_to_rgb("#00ffff"),  # cyan
        hex_to_rgb("#ff6600"),  # bright orange
        hex_to_rgb("#ffe600"),  # electric yellow
    ],
    "emerald": [
        hex_to_rgb("#00f5a0"),  # emerald
        hex_to_rgb("#00d9f5"),  # teal
        hex_to_rgb("#38ef7d"),  # bright green
        hex_to_rgb("#11998e"),  # deep teal
    ],
    "sunset": [
        hex_to_rgb("#ff416c"),  # crimson pink
        hex_to_rgb("#ff4b2b"),  # fiery orange
        hex_to_rgb("#ff9900"),  # golden amber
        hex_to_rgb("#8a2387"),  # deep violet
    ],
    "ocean": [
        hex_to_rgb("#00c6ff"),  # vivid sky blue
        hex_to_rgb("#0072ff"),  # royal blue
        hex_to_rgb("#0acffe"),  # cyan
        hex_to_rgb("#4facfe"),  # electric blue
    ],
    "fire": [
        hex_to_rgb("#ff1361"),  # red hot
        hex_to_rgb("#fff800"),  # bright yellow
        hex_to_rgb("#ff5e3a"),  # orange fire
        hex_to_rgb("#ff2a00"),  # flame red
    ],
}


class ColorScheme:
    """Manages color assignment for notes based on scheme, pitch, track, or channel."""

    def __init__(
        self,
        name: str = "neon",
        custom_left: str | None = None,
        custom_right: str | None = None,
        min_pitch: int = 21,
        max_pitch: int = 108,
    ):
        self.name = name.lower()
        self.min_pitch = min_pitch
        self.max_pitch = max(max_pitch, min_pitch + 1)
        self.custom_left = hex_to_rgb(custom_left) if custom_left else None
        self.custom_right = hex_to_rgb(custom_right) if custom_right else None

    def get_color(self, pitch: int, track: int = 0, channel: int = 0, velocity: int = 64) -> RGBA:
        """Returns (r, g, b, alpha) for a note."""
        # Handle custom left/right tracks if provided
        if self.custom_left and track == 0:
            return (*self.custom_left, 1.0)
        if self.custom_right and track > 0:
            return (*self.custom_right, 1.0)

        if self.name == "rainbow":
            # Smooth HSV spectrum across keyboard
            t = (pitch - self.min_pitch) / (self.max_pitch - self.min_pitch)
            t = max(0.0, min(1.0, t))
            # Map low pitch (bass) to deep purple/blue (0.75) down to hot red (0.0)
            hue = (0.75 - t * 0.75) % 1.0
            r, g, b = colorsys.hsv_to_rgb(hue, 0.88, 0.98)
            return (r, g, b, 1.0)

        if self.name == "tracks":
            # Palette cycle per track
            palette = PALETTES["neon"]
            rgb = palette[track % len(palette)]
            return (*rgb, 1.0)

        if self.name in PALETTES:
            palette = PALETTES[self.name]
            # Cycle through palette based on pitch octaves and fifths
            idx = (pitch * 7 + track) % len(palette)
            rgb = palette[idx]
            return (*rgb, 1.0)

        # Fallback to neon
        palette = PALETTES["neon"]
        idx = (pitch * 7 + track) % len(palette)
        rgb = palette[idx]
        return (*rgb, 1.0)
