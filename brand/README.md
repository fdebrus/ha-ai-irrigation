# Brand assets

Icons for the Irrigation Scheduler integration: a water droplet with clock
hands ("scheduled watering").

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

Home Assistant does **not** read icons from this repository. To make the icon
show up in *Settings → Devices & Services* (and to let the HACS workflow drop
its `ignore: brands`), these files must be submitted to
[`home-assistant/brands`](https://github.com/home-assistant/brands):

```
custom_integrations/irrigation_scheduler/icon.png      (256×256)
custom_integrations/irrigation_scheduler/icon@2x.png   (512×512)
```

Fork that repo, add the two files under the path above, and open a pull
request. Their CI validates the dimensions and transparency automatically.
Once it merges, remove `ignore: brands` from `.github/workflows/validate.yml`.
