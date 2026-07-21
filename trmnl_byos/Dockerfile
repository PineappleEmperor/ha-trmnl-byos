# Home Assistant passes the base image via BUILD_FROM, set per-arch in build.yaml to
# Playwright's Python image (Chromium + system deps preinstalled on Debian noble).
# A hardcoded FROM let the Supervisor builder substitute its own base, which is why
# `import playwright` failed at runtime.
ARG BUILD_FROM
FROM ${BUILD_FROM}
RUN pip install --no-cache-dir --break-system-packages flask "paho-mqtt>=2.0" pillow numpy
COPY dither.py renderer.py server.py /
# v3.0.0
CMD ["python3", "-u", "/server.py"]
