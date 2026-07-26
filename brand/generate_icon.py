"""Generate the Irrigation Scheduler brand icons.

Renders a water droplet with minimal clock hands ("scheduled watering") to the
sizes Home Assistant's ``home-assistant/brands`` repository expects:

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

    # Soft sheen in the upper-left, clipped to the droplet.
    sheen = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(sheen).ellipse(
        [cx - 0.20 * size, circle_cy - 0.34 * size, cx + 0.02 * size, circle_cy - 0.06 * size],
        fill=(255, 255, 255, 60),
    )
    empty = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    img = Image.alpha_composite(img, Image.composite(sheen, empty, mask))

    # Clock hands (10:10 pose) centred on the droplet's circle.
    draw = ImageDraw.Draw(img)

    def hand(angle_deg: float, length: float, width: float) -> None:
        a = math.radians(angle_deg)
        tx = cx + length * math.sin(a)
        ty = circle_cy - length * math.cos(a)
        draw.line([(cx, circle_cy), (tx, ty)], fill=WHITE, width=int(width))
        cap = width / 2
        draw.ellipse([tx - cap, ty - cap, tx + cap, ty + cap], fill=WHITE)

    hand(60, 0.72 * radius, 0.050 * size)  # minute -> "2"
    hand(-60, 0.52 * radius, 0.058 * size)  # hour -> "10"
    pivot = 0.052 * size
    draw.ellipse(
        [cx - pivot, circle_cy - pivot, cx + pivot, circle_cy + pivot], fill=WHITE
    )
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
