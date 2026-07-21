# TRMNL BYOS — in-addon dashboard renderer

**Date:** 2026-07-21
**Status:** Approved for implementation

## Problem

The BYOS addon (`server.py`) currently HTTP-proxies a pre-rendered screenshot from a
separate service, the official `usetrmnl/trmnl-home-assistant` addon at
`http://<screenshot-host>:10000`. That service shuts its headless Chromium down after 60s of
inactivity and cold-starts a fresh page per capture. On a cold page the MDI icon webfont and
entity-picture images have not finished loading when the screenshot fires, so icons render
blank intermittently.

## Goal

Replace the external screenshot service by folding a renderer into this addon. Keep **one
persistent, warm Chromium page** loaded on the dashboard. Home Assistant pushes state over
its websocket, so the single loaded page self-updates and never needs a reload. Fonts, icons,
and images load exactly once and stay cached in the live page, so captures are always
complete. Screenshot that warm page on the existing refresh cadence.

## Scope

- **In:** new `renderer.py` (browser lifecycle + capture + dithering); swap the image source
  inside `server.py`'s existing cache loop; config changes; Dockerfile base-image change;
  tests.
- **Out:** the MQTT layer, the TRMNL device HTTP API (`/api/setup`, `/api/display`,
  `/api/image`, `/api/sleep-image`, `/api/log`, `/healthz`), the sleep/wake light, and the
  refresh-rate control — all **unchanged**. This is a drop-in swap of where image bytes come
  from.

## Reproducing current output

The parameters the old service was driven with:

```
dashboard-eink?viewport=800x480&crop_x=0&crop_y=56&crop_width=800&crop_height=480
&theme=Graphite+E-ink+Light&lang=en-GB&dithering=&dither_method=ordered
&palette=gray-4&compression_level=6
```

Decoded into requirements:

- **Dashboard path:** `dashboard-eink`.
- **Render then crop:** render a viewport of `800×536`, then crop `x=0, y=56, w=800, h=480`
  to strip HA's 56px top header. Output is exactly `800×480`. The sidebar is hidden (docked)
  rather than cropped.
- **Dithering:** `ordered` (Bayer) to a **gray-4** palette (four grey levels — 2-bit — NOT
  1-bit black/white). Ordered dithering is chosen for e-ink because it is temporally stable:
  the pattern does not "crawl" between successive frames the way Floyd-Steinberg does, which
  avoids ghosting on repeated e-ink refreshes.
- **Theme:** `Graphite E-ink Light`. **Language:** `en-GB`.
- **PNG compression level:** 6.

## Architecture

```
                 ┌────────────────────── addon container ──────────────────────┐
                 │                                                              │
 HA websocket ──►│  renderer.py (warm Chromium + persistent page)              │
 (live updates)  │      start()    launch, auth, navigate, theme/lang, wait    │
                 │      capture()  screenshot warm page → crop → dither → PNG   │
                 │            ▲                                                  │
                 │            │ called by                                       │
                 │  server.py _refresh_cache loop  ──►  image_cache (unchanged) │
                 │            │                              ▲                   │
                 │            │                              │ served by         │
                 │  MQTT thread (unchanged)         /api/image, /api/display …  │
                 └──────────────────────────────────────────────────────────────┘
                                                            │
                                                            ▼  polls
                                                    TRMNL e-ink device
```

### `renderer.py`

A single class, `Renderer`, owning one Playwright browser and one page. Thread-safe
`capture()` guarded by a lock (the cache loop is the only caller, but the lock also serialises
against a concurrent restart).

- `start()`:
  1. Launch headless Chromium via Playwright (sync API).
  2. New context sized to `render_width × render_height` (`800×536`), device scale factor 1.
  3. Add an init script setting `localStorage.hassTokens` (long-lived token wrapped in HA's
     token JSON shape), `localStorage.dockedSidebar = "always_hidden"`, and
     `localStorage.selectedLanguage = lang`, so they are present before the app boots.
  4. Navigate to `{ha_url}/{dashboard_path}`.
  5. Wait for `networkidle`, then `page.evaluate("document.fonts.ready")`, then apply the
     theme via the `hass` object (`frontend/set_theme` websocket call) and a short settle
     wait.
- `capture() -> bytes`:
  1. `page.screenshot(clip={x:crop_x, y:crop_y, width:crop_width, height:crop_height})` on the
     already-warm page — **no navigation, no reload**.
  2. Pillow: load PNG → `convert("L")` → optional rotate → dither.
  3. Encode PNG at `compression_level`. Return bytes.
- `stop()`: close page, context, browser, Playwright. Idempotent.
- `alive` / `last_capture_at`: exposed for `/healthz`.

### Dithering (`dither.py` or a function in `renderer.py`)

`ordered_gray4(img_L) -> img_L`: apply an 8×8 Bayer threshold matrix (numpy) to an 8-bit
grayscale image, quantising to the four levels `{0, 85, 170, 255}`. Pure function,
deterministic, unit-testable in isolation. When `dither` is false, quantise to the palette
with no dithering (nearest level).

> Note: Pillow's built-in dithering is Floyd-Steinberg only, so ordered dithering is
> implemented directly with numpy rather than via Pillow or ImageMagick. This keeps the
> pipeline pure-Python, deterministic, and free of an external binary dependency.

### `server.py` integration

The `_refresh_cache` loop keeps its exact structure — interval selection (awake vs sleep),
`cache_wake` event, md5 hashing, change detection, `image_cache` fill under `cache_lock`. The
only change: replace the `requests.get(SCREENSHOT_URL, ...)` block with
`renderer.capture()`. The content type is always `image/png`.

Startup: construct the `Renderer`, call `start()` before spawning the cache thread. On
`start()` failure, log and let the cache loop retry (it will call a `capture()` that triggers
a lazy `start()`).

### Crash recovery

`capture()` is wrapped so that a dead browser/page (Playwright raises) triggers `stop()` then
`start()` and one retry. Failures are logged. `/healthz` gains
`renderer: {alive, last_capture_at}` so the existing health endpoint reports renderer state.

## Config changes (`config.yaml` + `_opt` reads in code)

Remove: `screenshot_url`.

Add (with defaults):

| Option | Default | Purpose |
|---|---|---|
| `ha_url` | `http://homeassistant:8123` | HA base URL |
| `ha_token` | `""` | long-lived access token |
| `dashboard_path` | `dashboard-eink` | dashboard to render |
| `theme` | `Graphite E-ink Light` | HA theme name |
| `lang` | `en-GB` | HA UI language |
| `render_width` | `800` | Chromium viewport width |
| `render_height` | `536` | Chromium viewport height (output height + crop_y) |
| `crop_x` | `0` | screenshot clip x |
| `crop_y` | `56` | screenshot clip y (strips header) |
| `crop_width` | `800` | output width |
| `crop_height` | `480` | output height |
| `rotation` | `0` | 0/90/180/270 |
| `dither` | `true` | ordered dither on/off |
| `palette` | `gray-4` | quantisation palette (only gray-4 implemented) |
| `compression_level` | `6` | PNG compression 0–9 |
| `zoom` | `1.0` | page zoom |

Kept unchanged: `port`, `api_key`, `refresh_rate`, `sleep_refresh_rate`,
`cache_refresh_rate`, and all `mqtt_*` / `mqtt_discovery_prefix` options.

## Dockerfile

Swap base `python:3.14-alpine` → `mcr.microsoft.com/playwright/python:v1.<pinned>-noble`
(Debian, Chromium + system deps preinstalled; Alpine/musl does not run Playwright's Chromium).
`pip install flask paho-mqtt pillow numpy` (drop `requests`). Bump the `# vX` marker comment.
Expect a larger image and ~300MB RAM for the warm browser.

## Error handling

- Missing/invalid `ha_token` → `start()` still loads the page; capture will show HA's login
  screen. Log a warning if the captured page title/URL indicates the auth screen. Not a hard
  crash — device keeps polling, health shows renderer alive.
- `capture()` exception → recover-and-retry once (see Crash recovery); on repeated failure the
  cache keeps its last-good bytes and `/api/image` continues serving them.
- Sleep mode unchanged: while the device is asleep the loop still runs on
  `sleep_refresh_rate`; the warm page stays open (cheap) so wake is instant.

## Testing

- **`dither` unit test:** feed a known 8-bit gradient, assert output pixels are all in
  `{0,85,170,255}` and that a fixed input yields a fixed byte hash (determinism).
- **`renderer` test:** point `start()` at a bundled static `test.html` (MDI-like glyphs +
  an `<img>`) served from `file://` or a tiny local HTTP server; assert `capture()` returns a
  non-blank `800×480` PNG in the gray-4 palette with the header region cropped. No live HA.
- **`server` test:** inject a fake renderer whose `capture()` returns fixed bytes; run one
  cache-loop iteration; assert `image_cache` fills, hash set, and `/api/image` returns the
  bytes with `image/png`. Endpoints exercised without a browser.

## Out-of-scope / future

- Multiple dashboards / playlist rotation (device supports it; not needed now).
- Palettes other than gray-4.
- Exposing render config over MQTT.
