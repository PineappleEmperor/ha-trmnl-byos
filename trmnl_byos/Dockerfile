# Base image is set directly here. Since Supervisor 2026.04.0 the legacy build.yaml /
# BUILD_FROM mechanism is gone — the Dockerfile FROM is authoritative.
# This image bundles the Chromium browsers + system deps (Debian noble), but per Playwright's
# docs it does NOT include the playwright pip package — install it here, pinned to the image
# version so it matches the bundled browsers.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble
RUN pip install --no-cache-dir --break-system-packages \
    flask "paho-mqtt>=2.0" pillow numpy playwright==1.61.0
COPY dither.py renderer.py server.py /
# v3.0.2
CMD ["python3", "-u", "/server.py"]
