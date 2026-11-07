# Branding

| File | Use |
| --- | --- |
| `pinch-bowl-mark.png` | Transparent mark for in-app UI (sidebar), marketing, and the hicolor app icon size set. Installed with the app. |
| `pinch-bowl-icon-final.jpg` | Locked full-colour concept (bowl + herb/spice pile on gray plate). Historical reference only; **not** installed. |

Sidebar loads the mark from `data/branding/pinch-bowl-mark.png` via `data_dir()`.

App icons are PNGs (not SVG): `data/icons/hicolor/*/apps/dev.kakwit.PinchBowl.png`, resized from `pinch-bowl-mark.png` (16–512). Regenerate with:

```bash
for s in 16 24 32 48 64 128 256 512; do
  magick data/branding/pinch-bowl-mark.png -filter Lanczos -resize "${s}x${s}" -strip \
    "data/icons/hicolor/${s}x${s}/apps/dev.kakwit.PinchBowl.png"
done
```
