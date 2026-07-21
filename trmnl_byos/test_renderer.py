"""Renderer test against a bundled static page. Requires playwright + its Chromium
(present in the addon's Playwright base image; not runnable in a bare env)."""

import functools
import http.server
import io
import os
import threading

from PIL import Image

from renderer import Renderer

_DIR = os.path.dirname(os.path.abspath(__file__))


def _serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=_DIR)
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def test_capture_crops_and_quantises():
    httpd, port = _serve()
    try:
        r = Renderer(
            ha_url=f"http://127.0.0.1:{port}", ha_token="", dashboard_path="test.html",
            lang="en-GB", render_width=800, render_height=536,
            crop_x=0, crop_y=56, crop_width=800, crop_height=480,
            rotation=0, dither=True, compression_level=6, zoom=1.0,
        )
        png = r.capture()
        r.stop()
    finally:
        httpd.shutdown()

    img = Image.open(io.BytesIO(png))
    assert img.size == (800, 480), img.size
    img_l = img.convert("L")
    assert set(img_l.get_flattened_data()) <= {0, 85, 170, 255}
    # The all-black 56px header was cropped, so the top output row is now the white page
    # body: it should be overwhelmingly bright rather than the near-black a header would give.
    top_row = [img_l.getpixel((x, 1)) for x in range(0, 800, 8)]
    assert sum(top_row) / len(top_row) > 200, sum(top_row) / len(top_row)


if __name__ == "__main__":
    test_capture_crops_and_quantises()
    print("PASS test_capture_crops_and_quantises")
