"""Persistent warm-page dashboard renderer.

Keeps one headless Chromium and one page loaded on the Home Assistant dashboard. HA pushes
state over its websocket, so the loaded page self-updates and never needs a reload. Fonts,
MDI icons, and entity images load exactly once and stay cached in the live page, so captures
are always complete — this is the fix for icons intermittently rendering blank.
"""

import io
import json
import logging
import threading
import time

import numpy as np
from PIL import Image

from dither import ordered_gray4

log = logging.getLogger(__name__)

_SETTLE_MS = 1000  # pause after theme applied, before first capture, to let repaint finish


class Renderer:
    def __init__(self, *, ha_url, ha_token, dashboard_path, lang,
                 render_width, render_height, crop_x, crop_y, crop_width, crop_height,
                 rotation, dither, compression_level, zoom):
        self.ha_url = ha_url.rstrip("/")
        self.ha_token = ha_token
        self.dashboard_path = dashboard_path.lstrip("/")
        self.lang = lang
        self.render_width = render_width
        self.render_height = render_height
        self.crop = {"x": crop_x, "y": crop_y, "width": crop_width, "height": crop_height}
        self.rotation = rotation
        self.dither = dither
        self.compression_level = compression_level
        self.zoom = zoom

        self._lock = threading.RLock()
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self.last_capture_at = None

    # -- lifecycle ----------------------------------------------------------

    @property
    def alive(self) -> bool:
        return self._page is not None

    def _init_script(self) -> str:
        # Long-lived token wrapped in HA's hassTokens shape so the frontend boots authed.
        tokens = {
            "access_token": self.ha_token,
            "token_type": "Bearer",
            "expires_in": 1800,
            "hassUrl": self.ha_url,
            "clientId": self.ha_url + "/",
            "expires": 9999999999999,
            "refresh_token": "",
        }
        return (
            f"window.localStorage.setItem('hassTokens', {json.dumps(json.dumps(tokens))});"
            f"window.localStorage.setItem('selectedLanguage', {json.dumps(json.dumps(self.lang))});"
            "window.localStorage.setItem('dockedSidebar', '\"always_hidden\"');"
        )

    def start(self):
        with self._lock:
            self._start_locked()

    def _start_locked(self):
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-extensions",
                "--hide-scrollbars",
                "--mute-audio",
            ],
        )
        self._context = self._browser.new_context(
            viewport={"width": self.render_width, "height": self.render_height},
            device_scale_factor=1,
        )
        self._context.add_init_script(self._init_script())
        self._page = self._context.new_page()

        url = f"{self.ha_url}/{self.dashboard_path}"
        self._page.goto(url, wait_until="networkidle", timeout=60000)
        self._page.evaluate("() => document.fonts.ready")
        self._apply_zoom()
        self._page.wait_for_timeout(_SETTLE_MS)
        self._check_auth()
        log.info("renderer: warm page loaded at %s", url)

    def _check_auth(self):
        try:
            authed = self._page.evaluate(
                "() => { const el = document.querySelector('home-assistant');"
                " return !!(el && el.hass && el.hass.user); }"
            )
        except Exception:
            authed = None
        if authed:
            log.info("renderer: authenticated to Home Assistant")
        else:
            log.warning(
                "renderer: page is NOT authenticated — captures will show the HA login "
                "screen, not the dashboard. Check ha_token and ha_url."
            )

    def _apply_zoom(self):
        # NOTE: do NOT call the `frontend.set_theme` service here. It sets the theme globally
        # in Home Assistant's frontend store for every user — it is not scoped to this headless
        # page. The e-ink theme must instead be set on the ha_token user's own profile
        # (Profile -> Theme), ideally a dedicated user, so it affects only what this renderer
        # loads. `theme` config is applied that way, out of band, not from here.
        if self.zoom and self.zoom != 1.0:
            self._page.evaluate("(z) => { document.body.style.zoom = z; }", self.zoom)

    def _teardown(self):
        for closer in (
            lambda: self._page and self._page.close(),
            lambda: self._context and self._context.close(),
            lambda: self._browser and self._browser.close(),
            lambda: self._pw and self._pw.stop(),
        ):
            try:
                closer()
            except Exception:
                pass
        self._page = self._context = self._browser = self._pw = None

    def stop(self):
        with self._lock:
            self._teardown()

    # -- capture ------------------------------------------------------------

    def capture(self) -> bytes:
        """Screenshot the warm page and return dithered PNG bytes. Recovers once on failure."""
        with self._lock:
            if self._page is None:
                self._start_locked()
            try:
                return self._capture_once()
            except Exception as exc:
                log.warning("renderer: capture failed (%s) — restarting browser", exc)
                self._teardown()
                self._start_locked()
                return self._capture_once()

    def _capture_once(self) -> bytes:
        png = self._page.screenshot(clip=self.crop, type="png")
        img = Image.open(io.BytesIO(png)).convert("L")
        if self.rotation in (90, 180, 270):
            img = img.rotate(-self.rotation, expand=True)
        arr = ordered_gray4(np.asarray(img), dither=self.dither)
        out = Image.fromarray(arr, mode="L")
        buf = io.BytesIO()
        out.save(buf, format="PNG", compress_level=self.compression_level)
        self.last_capture_at = time.time()
        return buf.getvalue()
