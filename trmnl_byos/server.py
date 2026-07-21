import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

import io

import paho.mqtt.client as mqtt
from flask import Flask, request, jsonify, Response
from PIL import Image, ImageDraw, ImageFont

from renderer import Renderer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

VERSION = "3.0.3"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_opts: dict = {}
try:
    with open("/data/options.json") as f:
        _opts = json.load(f)
except FileNotFoundError:
    pass

def _opt(key: str, default):
    return _opts.get(key, os.environ.get(key.upper(), default))

PORT                = int(_opt("port", 8765))
API_KEY             = str(_opt("api_key", "changeme"))
REFRESH_RATE        = int(_opt("refresh_rate", 180))
SLEEP_REFRESH_RATE  = int(_opt("sleep_refresh_rate", 3600))
CACHE_REFRESH_RATE  = int(_opt("cache_refresh_rate", 60))
HA_URL             = str(_opt("ha_url", "http://homeassistant:8123")).rstrip("/")
HA_TOKEN           = str(_opt("ha_token", ""))
DASHBOARD_PATH     = str(_opt("dashboard_path", "dashboard-eink"))
LANG               = str(_opt("lang", "en-GB"))
RENDER_WIDTH       = int(_opt("render_width", 800))
RENDER_HEIGHT      = int(_opt("render_height", 536))
CROP_X             = int(_opt("crop_x", 0))
CROP_Y             = int(_opt("crop_y", 56))
CROP_WIDTH         = int(_opt("crop_width", 800))
CROP_HEIGHT        = int(_opt("crop_height", 480))
ROTATION           = int(_opt("rotation", 0))
DITHER             = bool(_opt("dither", True))
COMPRESSION_LEVEL  = int(_opt("compression_level", 6))
ZOOM               = float(_opt("zoom", 1.0))
MQTT_HOST          = str(_opt("mqtt_host", "core-mosquitto"))
MQTT_PORT          = int(_opt("mqtt_port", 1883))
MQTT_USER          = str(_opt("mqtt_user", ""))
MQTT_PASSWORD      = str(_opt("mqtt_password", ""))
DISCOVERY_PREFIX   = str(_opt("mqtt_discovery_prefix", "homeassistant"))

# ---------------------------------------------------------------------------
# Sleep screen image (generated once at startup)
# ---------------------------------------------------------------------------

def _make_sleep_image() -> bytes:
    img = Image.new("L", (800, 480), color=255)
    draw = ImageDraw.Draw(img)
    text = "Zzz"
    try:
        font = ImageFont.load_default(size=120)
    except TypeError:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (800 - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (480 - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((x, y), text, fill=0, font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

SLEEP_IMAGE = _make_sleep_image()

# ---------------------------------------------------------------------------
# Image cache
# ---------------------------------------------------------------------------

image_cache: dict = {"bytes": None, "content_type": "image/png", "fetched_at": None, "hash": None}
cache_lock = threading.Lock()
cache_wake = threading.Event()

renderer = Renderer(
    ha_url=HA_URL, ha_token=HA_TOKEN, dashboard_path=DASHBOARD_PATH,
    lang=LANG,
    render_width=RENDER_WIDTH, render_height=RENDER_HEIGHT,
    crop_x=CROP_X, crop_y=CROP_Y, crop_width=CROP_WIDTH, crop_height=CROP_HEIGHT,
    rotation=ROTATION, dither=DITHER, compression_level=COMPRESSION_LEVEL, zoom=ZOOM,
)


def _capture_into_cache() -> bool:
    """Capture one frame and store it in image_cache. Returns True if the image changed."""
    data = renderer.capture()
    new_hash = hashlib.md5(data).hexdigest()[:8]
    with cache_lock:
        changed = new_hash != image_cache["hash"]
        image_cache["bytes"] = data
        image_cache["content_type"] = "image/png"
        image_cache["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        if changed:
            image_cache["hash"] = new_hash
    log.info("image cache refreshed (%d bytes)%s", len(data), " [changed]" if changed else "")
    return changed


def _refresh_cache():
    while True:
        try:
            _capture_into_cache()
        except Exception as exc:
            log.warning("image cache refresh failed: %s", exc)
        with state_lock:
            sleeping = not display_on
        interval = SLEEP_REFRESH_RATE if sleeping else CACHE_REFRESH_RATE
        cache_wake.wait(timeout=interval)
        cache_wake.clear()


# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

device_state: dict = {
    "mac": None,
    "friendly_name": None,
    "battery_voltage": None,
    "battery_pct": None,
    "rssi": None,
    "firmware": None,
    "width": None,
    "height": None,
    "last_seen": None,
}

display_on = True          # False → next /api/display response sleeps the device
current_refresh_rate = REFRESH_RATE
state_lock = threading.Lock()

# ---------------------------------------------------------------------------
# MQTT
# ---------------------------------------------------------------------------

DEVICE_BLOCK = {
    "identifiers": ["trmnl_byos"],
    "name": "TRMNL",
    "model": "TRMNL e-ink display",
    "manufacturer": "TRMNL",
}

TOPIC_DISPLAY_STATE       = "trmnl/display/state"
TOPIC_DISPLAY_SET         = "trmnl/display/set"
TOPIC_SENSOR_STATE        = "trmnl/sensor/state"
TOPIC_REFRESH_RATE_STATE  = "trmnl/refresh_rate/state"
TOPIC_REFRESH_RATE_SET    = "trmnl/refresh_rate/set"

DISCOVERY_CONFIGS = {
    # Light entity — ON = display active, OFF = display sleeping
    f"{DISCOVERY_PREFIX}/light/trmnl_display/config": {
        "name": "TRMNL Display",
        "unique_id": "trmnl_display_light",
        "state_topic": TOPIC_DISPLAY_STATE,
        "command_topic": TOPIC_DISPLAY_SET,
        "payload_on": "ON",
        "payload_off": "OFF",
        "icon": "mdi:monitor",
        "device": DEVICE_BLOCK,
    },
    # Battery voltage sensor
    f"{DISCOVERY_PREFIX}/sensor/trmnl_battery_voltage/config": {
        "name": "TRMNL Battery Voltage",
        "unique_id": "trmnl_battery_voltage",
        "state_topic": TOPIC_SENSOR_STATE,
        "value_template": "{{ value_json.battery_voltage }}",
        "unit_of_measurement": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:battery",
        "device": DEVICE_BLOCK,
    },
    # Battery percent sensor
    f"{DISCOVERY_PREFIX}/sensor/trmnl_battery_pct/config": {
        "name": "TRMNL Battery",
        "unique_id": "trmnl_battery_pct",
        "state_topic": TOPIC_SENSOR_STATE,
        "value_template": "{{ value_json.battery_pct }}",
        "unit_of_measurement": "%",
        "device_class": "battery",
        "state_class": "measurement",
        "device": DEVICE_BLOCK,
    },
    # RSSI sensor
    f"{DISCOVERY_PREFIX}/sensor/trmnl_rssi/config": {
        "name": "TRMNL Signal",
        "unique_id": "trmnl_rssi",
        "state_topic": TOPIC_SENSOR_STATE,
        "value_template": "{{ value_json.rssi }}",
        "unit_of_measurement": "dBm",
        "device_class": "signal_strength",
        "state_class": "measurement",
        "entity_category": "diagnostic",
        "device": DEVICE_BLOCK,
    },
    # Refresh rate number entity
    f"{DISCOVERY_PREFIX}/number/trmnl_refresh_rate/config": {
        "name": "TRMNL Refresh Rate",
        "unique_id": "trmnl_refresh_rate",
        "state_topic": TOPIC_REFRESH_RATE_STATE,
        "command_topic": TOPIC_REFRESH_RATE_SET,
        "min": 60,
        "max": 3600,
        "step": 30,
        "unit_of_measurement": "s",
        "icon": "mdi:timer-refresh",
        "entity_category": "config",
        "device": DEVICE_BLOCK,
    },
    # Last seen sensor
    f"{DISCOVERY_PREFIX}/sensor/trmnl_last_seen/config": {
        "name": "TRMNL Last Seen",
        "unique_id": "trmnl_last_seen",
        "state_topic": TOPIC_SENSOR_STATE,
        "value_template": "{{ value_json.last_seen }}",
        "device_class": "timestamp",
        "entity_category": "diagnostic",
        "device": DEVICE_BLOCK,
    },
}

_mqtt_client: mqtt.Client = None


def _publish_discovery(client: mqtt.Client):
    for topic, payload in DISCOVERY_CONFIGS.items():
        client.publish(topic, json.dumps(payload), retain=True)
    log.info("MQTT discovery published")


def _publish_sensor_state(client: mqtt.Client):
    with state_lock:
        payload = {
            "battery_voltage": device_state["battery_voltage"],
            "battery_pct": device_state["battery_pct"],
            "rssi": device_state["rssi"],
            "last_seen": device_state["last_seen"],
        }
    client.publish(TOPIC_SENSOR_STATE, json.dumps(payload), retain=True)


def _publish_display_state(client: mqtt.Client):
    global display_on
    client.publish(TOPIC_DISPLAY_STATE, "ON" if display_on else "OFF", retain=True)


def _publish_refresh_rate_state(client: mqtt.Client):
    global current_refresh_rate
    client.publish(TOPIC_REFRESH_RATE_STATE, str(current_refresh_rate), retain=True)


def _on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        log.info("MQTT connected to %s:%s", MQTT_HOST, MQTT_PORT)
        client.subscribe(TOPIC_DISPLAY_SET)
        client.subscribe(TOPIC_REFRESH_RATE_SET)
        _publish_discovery(client)
        _publish_display_state(client)
        _publish_refresh_rate_state(client)
    else:
        log.warning("MQTT connect failed: reason_code=%s", reason_code)


def _on_message(client, userdata, msg):
    global display_on, current_refresh_rate
    if msg.topic == TOPIC_DISPLAY_SET:
        payload = msg.payload.decode().strip().upper()
        with state_lock:
            display_on = payload == "ON"
        log.info("display_on set to %s via MQTT", display_on)
        _publish_display_state(client)
        if display_on:
            cache_wake.set()  # wake cache thread immediately so image is fresh when device polls
    elif msg.topic == TOPIC_REFRESH_RATE_SET:
        try:
            rate = int(float(msg.payload.decode().strip()))
            rate = max(60, min(3600, rate))
            with state_lock:
                current_refresh_rate = rate
            log.info("refresh_rate set to %ds via MQTT", rate)
            _publish_refresh_rate_state(client)
        except ValueError:
            log.warning("invalid refresh_rate payload: %s", msg.payload)


def _mqtt_thread():
    global _mqtt_client
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.on_connect = _on_connect
    client.on_message = _on_message
    if MQTT_USER:
        client.username_pw_set(MQTT_USER, MQTT_PASSWORD)

    _mqtt_client = client

    while True:
        try:
            client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
            client.loop_forever()
        except Exception as exc:
            log.warning("MQTT error: %s — reconnecting in 15s", exc)
            time.sleep(15)


# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------

app = Flask(__name__)


def _update_device_state(headers):
    """Parse TRMNL request headers and update device_state."""
    try:
        voltage = float(headers.get("Battery-Voltage", 0) or 0)
        pct = round(max(0.0, min(100.0, (voltage - 3.0) / (4.2 - 3.0) * 100)), 1) if voltage else None
    except ValueError:
        voltage, pct = None, None

    try:
        rssi = int(headers.get("RSSI", 0) or 0) or None
    except ValueError:
        rssi = None

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")

    with state_lock:
        if voltage:
            device_state["battery_voltage"] = round(voltage, 2)
            device_state["battery_pct"] = pct
        if rssi is not None:
            device_state["rssi"] = rssi
        device_state["last_seen"] = now_iso
        device_state["firmware"] = headers.get("FW-Version") or device_state["firmware"]
        device_state["width"]    = headers.get("Width")      or device_state["width"]
        device_state["height"]   = headers.get("Height")     or device_state["height"]

    if _mqtt_client and _mqtt_client.is_connected():
        _publish_sensor_state(_mqtt_client)


@app.route("/api/setup", methods=["GET"])
def setup():
    mac = request.headers.get("ID", "unknown")
    friendly = f"TRMNL-{mac[-5:]}" if mac != "unknown" else "TRMNL"
    with state_lock:
        device_state["mac"] = mac
        device_state["friendly_name"] = friendly
    log.info("setup: mac=%s", mac)
    return jsonify({"status": 200, "api_key": API_KEY, "friendly_name": friendly})


@app.route("/api/display", methods=["GET"])
def display():
    token = request.headers.get("Access-Token", "")
    if token != API_KEY:
        return jsonify({"status": 401, "error": "unauthorized"}), 401

    _update_device_state(request.headers)

    with state_lock:
        sleeping = not display_on
        poll_rate = current_refresh_rate

    host = request.headers.get("Host", "localhost:10000")

    if sleeping:
        log.info("display: sleep mode — serving sleep screen")
        return jsonify({
            "status": 0,
            "image_url": f"http://{host}/api/sleep-image",
            "filename": "sleep.png",
            "render_mode": 0,
            "refresh_rate": SLEEP_REFRESH_RATE,
            "reset_firmware": False,
            "update_firmware": False,
            "firmware_url": "",
            "special_function": "",
            "update_playlist": False,
        })
    with cache_lock:
        h = image_cache["hash"] or "0"
    image_url = f"http://{host}/api/image?h={h}"
    log.info("display: serving %s", image_url)
    return jsonify({
        "status": 0,
        "image_url": image_url,
        "filename": f"dashboard_{h}.png",
        "render_mode": 0,
        "refresh_rate": poll_rate,
        "reset_firmware": False,
        "update_firmware": False,
        "firmware_url": "",
        "special_function": "",
        "update_playlist": False,
    })


@app.route("/api/image", methods=["GET"])
def image():
    with cache_lock:
        data = image_cache["bytes"]
        ct = image_cache["content_type"]
    if data is None:
        return Response("image not yet cached", status=503, mimetype="text/plain")
    return Response(data, status=200, mimetype=ct)


@app.route("/api/sleep-image", methods=["GET"])
def sleep_image():
    return Response(SLEEP_IMAGE, status=200, mimetype="image/png")


@app.route("/api/log", methods=["POST"])
def device_log():
    payload = request.get_json(silent=True) or request.get_data(as_text=True)
    log.info("device log: %s", payload)
    return jsonify({"status": 200})


@app.route("/healthz")
def healthz():
    with state_lock:
        with cache_lock:
            cache_info = {"fetched_at": image_cache["fetched_at"], "size": len(image_cache["bytes"]) if image_cache["bytes"] else 0, "hash": image_cache["hash"]}
        renderer_info = {"alive": renderer.alive, "last_capture_at": renderer.last_capture_at}
        return jsonify({"version": VERSION, "ok": True, "device": dict(device_state), "display_on": display_on, "cache": cache_info, "renderer": renderer_info})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _log_environment():
    import platform
    import sys
    distro = "?"
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    distro = line.split("=", 1)[1].strip().strip('"')
                    break
    except Exception:
        pass
    log.info("env: %s | python %s (%s)", distro, platform.python_version(), sys.executable)
    try:
        import playwright
        log.info("env: playwright import OK (%s)", playwright.__file__)
    except Exception as exc:
        log.warning("env: playwright import FAILED (%s) — base image is wrong", exc)


if __name__ == "__main__":
    log.info("TRMNL BYOS server v%s starting on port %s", VERSION, PORT)
    _log_environment()
    # The renderer owns Playwright's sync objects, which are bound to the thread that
    # creates them. It is therefore started lazily inside capture(), on the cache thread —
    # never from the main thread — so every Playwright call happens on that one thread.
    threading.Thread(target=_mqtt_thread, daemon=True).start()
    threading.Thread(target=_refresh_cache, daemon=True).start()
    app.run(host="0.0.0.0", port=PORT, debug=False)
