# Base image is set directly here. Since Supervisor 2026.04.0 the legacy build.yaml /
# BUILD_FROM mechanism is gone — the Dockerfile FROM is authoritative.
# Playwright's Python image ships Chromium + system deps on Debian noble; Alpine/musl
# cannot run Playwright's Chromium.
FROM mcr.microsoft.com/playwright/python:v1.61.0-noble
RUN pip install --no-cache-dir --break-system-packages flask "paho-mqtt>=2.0" pillow numpy
COPY dither.py renderer.py server.py /
# v3.0.1
CMD ["python3", "-u", "/server.py"]
