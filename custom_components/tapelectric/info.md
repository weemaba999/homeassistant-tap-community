# Tap Electric Charger — Home Assistant Community Integration

An unofficial, open-source Home Assistant integration for EV chargers
managed through Tap Electric's developer API. This project is built
and maintained by the community and is **NOT** affiliated with,
endorsed by, or sponsored by Tap Electric B.V.

"Tap Electric" is a trademark of Tap Electric B.V. This integration
merely interoperates with their publicly-documented API and —
optionally, at user discretion — the same management API their mobile
app uses.

## Features

- **Basic mode** (API key only):
  - Charger status, connector state, online/fault binary sensors
  - Session list, session energy, session duration
  - Per-measurand sensors (Energy by default; Power, Current, Voltage,
    SoC, Temperature available but hidden unless your charger emits them)
  - Remote reset, pause, resume, charge-current limit
- **Advanced mode** (optional — email + password):
  - Live session energy (updates every ~30 s, no waiting for the session to close)
  - Driver name, location name, started-at timestamp
  - Session duration computed from the authoritative timestamp
- Webhook support for push updates (`SessionStarted`, `SessionUpdated`,
  `SessionEnded`, `TokenAuthorization`)
- Full translation: English, Dutch (Belgian), German (machine), French (machine)
- HA Repairs integration for auth failures, offline chargers, and
  disabled writes

## Installation

1. Install via HACS (recommended) or drop
   `custom_components/tapelectric/` into your HA config folder.
2. Restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Tap Electric Charger**.
4. Paste your `sk_` API key from **web.tapelectric.app → Account → API management**.
5. Choose whether to enable advanced mode.

## Screenshots

_Screenshots coming soon._

## Known limitations

- Certain charger models (e.g. EVBox Elvi) only emit the `Energy`
  measurand over OCPP. Power/Current/Voltage/SoC/Temperature entities
  are shipped disabled-by-default; enable them if your charger is
  known to emit them.
- `GET /charger-sessions/{id}` returns 404 on the public API
  (confirmed 2026-04-23). Single-session detail is only available
  through the management API / advanced mode.
- Brand assets are intentionally absent — the integration shows a
  generic icon until Tap Electric B.V. grants explicit permission or
  the community contributes original artwork. See `brands/README.md`.

## Links

- [Source & issues](https://github.com/weemaba999/homeassistant-tap-community)
- [API inventory](https://github.com/weemaba999/homeassistant-tap-community/blob/main/docs/API_INVENTORY.md)
- [Changelog](https://github.com/weemaba999/homeassistant-tap-community/blob/main/CHANGELOG.md)
