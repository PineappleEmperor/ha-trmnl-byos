# PineappleEmperor TRMNL BYOS — Home Assistant add-on repository

A custom Home Assistant **add-on** repository containing a minimal TRMNL BYOS (Bring Your Own
Server) backend for a [TRMNL](https://usetrmnl.com) e-ink display.

The add-on renders a Home Assistant dashboard with a **persistent warm headless Chromium
page**, dithers it for e-ink, and serves it to the device. Because the page stays loaded and
Home Assistant pushes state over its websocket, fonts and MDI icons load once and stay
cached — so captured screenshots never drop icons the way a cold-started renderer does. It
also exposes device state (battery, signal, sleep) to Home Assistant over MQTT.

## Install

1. Settings → Add-ons → Add-on Store → ⋮ (top-right) → **Repositories**.
2. Add: `https://github.com/PineappleEmperor/ha-trmnl-byos`
3. Install **TRMNL BYOS Server** from the store.
4. Set `ha_token` (a Home Assistant long-lived access token) and the other options, then
   start. See the add-on's Documentation tab for all options.

## Add-ons

- [`trmnl_byos/`](trmnl_byos/) — TRMNL BYOS Server.

> Note: this is an **add-on** repository (installed via the Add-on Store → Repositories), not
> a HACS repository. HACS does not manage add-ons.
