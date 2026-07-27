"""Generate the Irrigation Scheduler brand icons.

Renders a water droplet carrying a four-point AI sparkle ("AI-driven
watering") to the sizes Home Assistant's ``home-assistant/brands`` repository
expects:

    icon.png      256x256
    icon@2x.png   512x512

The mark is drawn at high resolution and downsampled with LANCZOS so the edges
are anti-aliased despite Pillow's aliased primitives. Pillow is the only
dependency (``pip install pillow``); this script is a build tool for the brand
assets and is not part of the integration.

Usage:
    python brand/generate_icon.py
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

SUPERSAMPLE = 2048
TOP_COLOR = (0x58, 0xC4, 0xF5)  # light blue (droplet top)
BOTTOM_COLOR = (0x0B, 0x6F, 0xB8)  # deep blue (droplet bottom)
WHITE = (255, 255, 255, 255)


def _lerp(a: tuple[int, ...], b: tuple[int, ...], t: float) -> tuple[int, int, int]:
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


def render(size: int) -> Image.Image:
    """Render the icon at ``size`` x ``size`` pixels on a transparent canvas."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cx = size / 2
    droplet_height = 0.86 * size
    apex_y = (size - droplet_height) / 2
    radius = 0.30 * size
    circle_cy = apex_y + droplet_height - radius
    dist = circle_cy - apex_y
    beta = math.acos(radius / dist)
    tangent_dx = radius * math.sin(beta)
    tangent_y = circle_cy - radius * math.cos(beta)

    # Droplet silhouette = circle + triangle(apex, tangent-left, tangent-right).
    mask = Image.new("L", (size, size), 0)
    md = ImageDraw.Draw(mask)
    md.ellipse(
        [cx - radius, circle_cy - radius, cx + radius, circle_cy + radius], fill=255
    )
    md.polygon(
        [(cx, apex_y), (cx - tangent_dx, tangent_y), (cx + tangent_dx, tangent_y)],
        fill=255,
    )

    # Vertical gradient clipped to the droplet.
    gradient = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gpx = gradient.load()
    bottom_y = apex_y + droplet_height
    for y in range(size):
        t = min(1.0, max(0.0, (y - apex_y) / (bottom_y - apex_y)))
        r, g, b = _lerp(TOP_COLOR, BOTTOM_COLOR, t)
        for x in range(size):
            gpx[x, y] = (r, g, b, 255)
    img = Image.composite(gradient, img, mask)

    # Soft sheen in the upper-left, clipped to the droplet. Kept subtle so it
    # does not compete with the sparkle.
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).ellipse(
        [
            cx - 0.24 * size,
            circle_cy - 0.36 * size,
            cx - 0.06 * size,
            circle_cy - 0.12 * size,
        ],
        fill=(255, 255, 255, 42),
    )
    empty = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img = Image.alpha_composite(img, Image.composite(sheen, empty, mask))

    # Four-point AI sparkle (the Copilot/Gemini-style glyph) centred on the
    # droplet's circle, with a small echo sparkle upper-right -- reads as
    # "AI-driven" without lettering, and survives 32 px.
    draw = ImageDraw.Draw(img)

    def sparkle(sx: float, sy: float, outer: float, inner_ratio: float) -> None:
        points = []
        for i in range(8):
            angle = math.radians(i * 45)
            rad = outer if i % 2 == 0 else outer * inner_ratio
            points.append((sx + rad * math.sin(angle), sy - rad * math.cos(angle)))
        draw.polygon(points, fill=WHITE)

    sparkle(cx, circle_cy + 0.01 * size, 0.74 * radius, 0.22)
    sparkle(cx + 0.13 * size, circle_cy - 0.16 * size, 0.27 * radius, 0.26)
    return img


def main() -> None:
    """Render the master icon and write the two brand sizes."""
    here = Path(__file__).resolve().parent
    master = render(SUPERSAMPLE)
    for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
        master.resize((size, size), Image.LANCZOS).save(here / name)
        print(f"wrote {name} ({size}x{size})")  # noqa: T201


if __name__ == "__main__":
    main()
