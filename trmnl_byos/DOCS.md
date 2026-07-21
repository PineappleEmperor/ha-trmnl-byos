# TRMNL BYOS Server

Serves a Home Assistant dashboard to a TRMNL e-ink display, and publishes device state to
Home Assistant over MQTT.

The add-on keeps **one persistent, warm headless Chromium page** loaded on your dashboard.
Home Assistant pushes state changes to it over the websocket, so the page stays current
without ever reloading. Fonts, MDI icons, and entity images load once and stay cached, which
is why captures don't drop icons. It screenshots that warm page on a refresh cadence, crops
the header, and applies ordered (Bayer) dithering to a 4-level grey palette for e-ink.

## Setup

> **Use a dedicated Home Assistant user for the token.** The renderer logs in as the token's
> user, and things like the selected theme are that user's own settings. A dedicated (non-admin)
> user keeps the add-on from touching your account and limits its access. Set the e-ink theme on
> **that user's** Profile → Theme — the renderer does not (and must not) change the theme
> globally.

1. Create a dedicated HA user (Settings → People → Users). Log in as them once, set
   Profile → **Theme** to your e-ink theme (e.g. `Graphite E-ink Light`).
2. As that user: **Profile → Long-Lived Access Tokens → Create Token**. Copy it.
2. In the add-on **Configuration**, set:
   - `ha_token` — the token you just created (required; without it the renderer captures the
     login screen, and the add-on log warns `page is NOT authenticated`).
   - `ha_url` — your Home Assistant URL. `http://homeassistant:8123` works from the add-on on
     most HAOS installs.
   - `dashboard_path` — the dashboard/view to render, e.g. `dashboard-eink`.
3. Start the add-on. Check the log for `renderer: authenticated to Home Assistant` and
   `warm page loaded`. Visit `http://<host>:8765/healthz` to confirm `renderer.alive`.
4. Point your TRMNL device's server URL at this add-on. It polls `/api/display`, then fetches
   `/api/image`.

## Options

| Option | Default | Purpose |
|---|---|---|
| `port` | `8765` | HTTP port the device polls |
| `api_key` | `changeme` | token the device must send as `Access-Token` |
| `refresh_rate` | `180` | seconds between device polls while awake |
| `sleep_refresh_rate` | `3600` | seconds between polls while sleeping |
| `cache_refresh_rate` | `60` | seconds between screenshot captures while awake |
| `ha_url` | `http://homeassistant:8123` | Home Assistant base URL |
| `ha_token` | `""` | long-lived access token (required) |
| `dashboard_path` | `dashboard-eink` | dashboard/view to render |
| `lang` | `en-GB` | Home Assistant UI language (set browser-side, not global) |
| `render_width` | `800` | Chromium viewport width |
| `render_height` | `536` | Chromium viewport height (output height + `crop_y`) |
| `crop_x` / `crop_y` | `0` / `56` | top-left of the screenshot crop (strips the 56px header) |
| `crop_width` / `crop_height` | `800` / `480` | output size |
| `rotation` | `0` | 0 / 90 / 180 / 270 |
| `dither` | `true` | ordered dithering to gray-4 on/off |
| `compression_level` | `6` | PNG compression 0–9 |
| `zoom` | `1.0` | page zoom |
| `mqtt_*` | — | MQTT broker connection + discovery prefix |

## MQTT entities

Auto-discovered under the `TRMNL` device: a **Display** light (on = active, off = sleep), and
sensors for battery voltage, battery %, signal (RSSI), and last-seen. Toggling the light off
puts the device to sleep on its next poll.

## Health

`GET /healthz` returns version, device state, cache info, and
`renderer: {alive, last_capture_at}`.
