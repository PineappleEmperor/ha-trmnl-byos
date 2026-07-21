# Partial refresh — server-side setup (future work)

Not implemented. This documents what the **addon** would need to do to support a future
e-ink device capable of partial (region) refresh, so the work is scoped when such a device
exists.

## Prerequisites (outside the addon)

Partial refresh cannot be delivered by the server alone. Two things must exist first:

1. **Device firmware that supports region updates.** The current TRMNL OG firmware does
   full-frame refreshes only. A different device/firmware must expose a partial-update path.
2. **A wire protocol for deltas.** The TRMNL BYOS API only has "fetch this full image URL"
   (`/api/display` → `image_url` → `/api/image`). There is no endpoint or payload format for
   "update rectangle (x, y, w, h) with these pixels." That contract has to be defined jointly
   with whatever the device speaks.

Until both exist, the server has nothing to talk to — this stays on the shelf.

## Server side (the easy part)

The renderer already holds everything needed: it keeps one warm page and produces a full
800×480 gray-4 frame each cycle, and `image_cache` already stores the last frame + its hash.

### 1. Keep the previous frame

Retain the last captured frame alongside the current one (as a numpy array, pre-PNG, so diffs
are cheap). `image_cache` currently keeps only the latest bytes — add `prev_arr` / `curr_arr`.

### 2. Diff to changed regions

With numpy:

```python
import numpy as np

def changed_bbox(prev: np.ndarray, curr: np.ndarray):
    """Return (x, y, w, h) bounding box of changed pixels, or None if identical."""
    mask = prev != curr
    if not mask.any():
        return None
    ys, xs = np.where(mask.any(axis=1)), np.where(mask.any(axis=0))
    y0, y1 = ys[0][0], ys[0][-1]
    x0, x1 = xs[0][0], xs[0][-1]
    return int(x0), int(y0), int(x1 - x0 + 1), int(y1 - y0 + 1)
```

A single bounding box is simplest. If updates are scattered (clock in one corner, sensor in
another), a **tile approach** is better: split the frame into an NxM grid (e.g. 16×16 px
tiles), mark which tiles changed, and send only those. This avoids one huge bbox when two far
-apart pixels change. Choose based on what the firmware accepts (one rect vs a tile list).

### 3. Alignment constraints

E-ink controllers usually require partial-update regions aligned to a byte/pixel boundary
(commonly x and width as multiples of 8, sometimes 4). Snap the bbox outward to the required
alignment before sending, or the controller will reject/garble it. Make the multiple a config
value.

### 4. Dithering caveat

Ordered (Bayer) dithering is **position-dependent**: the same grey at a different (x, y)
gets a different threshold. So a region cut from a freshly dithered full frame lines up with
the rest — good. But if the firmware composites the region onto its existing buffer, dither
the **full** frame first, then crop the region out of it (never dither the cropped region in
isolation, or the pattern seams at the edges).

### 5. Endpoint

Add a device-specific endpoint mirroring whatever the firmware requests, e.g.:

```
GET /api/partial?since=<last_hash>
→ 200 { "x":…, "y":…, "w":…, "h":…, "hash":…, "image_url": "/api/partial-image?h=…" }
   or  204 No Content   when nothing changed since <last_hash>
```

Keep the existing `/api/display` + full `/api/image` as the fallback for the first frame,
after sleep/wake, and when the changed area exceeds a threshold (a large change is cheaper as
a full refresh, and full refreshes periodically clear e-ink ghosting).

### 6. Full-refresh cadence

Partial refreshes accumulate ghosting on e-ink. Force a periodic full refresh (every N
partials, or on wake) even if the diff is small. Make N configurable.

## Integration points in current code

- `renderer.py` — `capture()` already returns the full frame; have it also return/retain the
  pre-PNG numpy array so diffs don't re-decode.
- `server.py` — `_capture_into_cache()` is where prev/curr would be tracked and the bbox
  computed; add the new endpoint next to `/api/image`.
- `dither.py` — add `changed_bbox` (or a tiled variant) here; it is pure and unit-testable.

## Effort

Server side: small — a diff function, a couple of endpoints, prev-frame retention. The real
cost is entirely in (a) having a device that supports partial refresh and (b) agreeing the
wire format with its firmware. Do not build until both prerequisites are real.
