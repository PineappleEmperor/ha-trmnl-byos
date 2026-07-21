"""Server cache + endpoint test with a fake renderer (no browser). Requires flask
(present in the addon image)."""

import server


class _FakeRenderer:
    def __init__(self, data):
        self._data = data
        self.alive = True
        self.last_capture_at = 123.0

    def capture(self):
        return self._data


def _reset_cache():
    with server.cache_lock:
        server.image_cache.update(bytes=None, content_type="image/png", fetched_at=None, hash=None)


def test_capture_fills_cache_and_endpoint_serves_it():
    _reset_cache()
    server.renderer = _FakeRenderer(b"PNGBYTES")

    changed = server._capture_into_cache()
    assert changed is True
    assert server.image_cache["bytes"] == b"PNGBYTES"
    assert server.image_cache["hash"]

    # Same bytes again -> hash unchanged, changed flag False.
    assert server._capture_into_cache() is False

    client = server.app.test_client()
    resp = client.get("/api/image")
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data == b"PNGBYTES"


def test_healthz_reports_renderer():
    server.renderer = _FakeRenderer(b"X")
    resp = server.app.test_client().get("/healthz")
    body = resp.get_json()
    assert body["renderer"]["alive"] is True
    assert body["renderer"]["last_capture_at"] == 123.0


if __name__ == "__main__":
    test_capture_fills_cache_and_endpoint_serves_it()
    print("PASS test_capture_fills_cache_and_endpoint_serves_it")
    test_healthz_reports_renderer()
    print("PASS test_healthz_reports_renderer")
