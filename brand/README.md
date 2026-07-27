# Brand assets

Icons for the Irrigation Scheduler integration: a water droplet carrying a
four-point AI sparkle ("AI-driven watering").

| File | Size | Purpose |
| --- | --- | --- |
| `icon.png` | 256×256 | Home Assistant integration icon |
| `icon@2x.png` | 512×512 | high-DPI variant |

## Regenerate

```bash
pip install pillow
python brand/generate_icon.py
```

`generate_icon.py` is a build tool for these assets, not part of the
integration — it lives outside `custom_components/` and is excluded from lint.

## Publishing to Home Assistant

Since Home Assistant **2026.3**, custom integrations ship their own brand
images: the PNGs are copied into
`custom_components/irrigation_scheduler/brand/`, which HA serves through its
local brands proxy (`/api/brands/integration/irrigation_scheduler/…`). Local
images take priority over the brands CDN, so nothing else is required — after
regenerating, re-copy both files there.

This directory remains the source of truth (it also holds the generator).
Optionally the same files can still be submitted to
[`home-assistant/brands`](https://github.com/home-assistant/brands) under
`custom_integrations/irrigation_scheduler/` — that covers installs older than
2026.3 and lets the HACS workflow drop its `ignore: brands`.
Once it merges, remove `ignore: brands` from `.github/workflows/validate.yml`.
