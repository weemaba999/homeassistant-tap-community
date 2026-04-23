# Tap Electric Charger — Home Assistant Community Integration

[![HACS Default](https://img.shields.io/badge/HACS-custom-blue.svg)](https://hacs.xyz)
[![Validate](https://github.com/weemaba999/homeassistant-tap-community/actions/workflows/validate.yml/badge.svg)](https://github.com/weemaba999/homeassistant-tap-community/actions/workflows/validate.yml)
[![Release](https://img.shields.io/github/v/release/weemaba999/homeassistant-tap-community?include_prereleases)](https://github.com/weemaba999/homeassistant-tap-community/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org)

A community-built Home Assistant integration for EV chargers managed
through **Tap Electric**. Runs on top of Tap's developer API for
read/write, and — optionally — the same management API their mobile
app uses for live session metadata.

> ⚠️ **This is an UNOFFICIAL community integration.**
> Not affiliated with, endorsed by, or sponsored by Tap Electric B.V.
> "Tap Electric" is a trademark of Tap Electric B.V. — this project
> merely interoperates with their publicly-documented API.

## Screenshots

_Screenshots pending — please PR yours if you have a good shot of the
device page or an energy-dashboard hookup._

## Features

- **Basic mode** (single `sk_` API key):
  - Charger status, connector status, online / fault binary sensors
  - Session list (configurable retention), session energy, session
    duration, last session energy
  - Per-measurand sensors (Energy default-enabled; Power, Current,
    Voltage, SoC, Temperature available but default-hidden because
    many chargers only emit a subset)
  - Remote: pause, resume, set charge-current limit, soft/hard reset
  - Webhook support for push updates
- **Advanced mode** (email + password, opt-in):
  - Live session energy (refreshes every ~30 s, no waiting for a
    session close)
  - Driver name, location name, started-at timestamp
  - Richer closed-session detail (cost currency, transaction id,
    retail tariff breakdown — surfaces on the `last_session_energy`
    sensor's attributes with `source: "management"`)
  - Automatic reauth prompt after 3 consecutive auth failures
- **Localized**: English, Dutch (Belgian flavor), German (machine-
  translated — native review welcome), French (machine-translated —
  native review welcome)
- **Graceful degradation**: advanced-mode hiccups never break basic
  mode; fallback happens transparently with a `source` attribute so
  you can tell which tier a given reading came from

## Installation

### Via HACS

1. **HACS → Integrations → ⋮ → Custom repositories**
2. URL: `https://github.com/weemaba999/homeassistant-tap-community`
3. Category: **Integration**
4. **Install** → **Restart Home Assistant**

### Manual

Copy `custom_components/tapelectric/` into your HA config folder and
restart:

```
/config/custom_components/tapelectric/
    __init__.py, api.py, api_management.py, auth_firebase.py,
    binary_sensor.py, button.py, config_flow.py, const.py,
    coordinator.py, device_action.py, device_condition.py,
    device_trigger.py, manifest.json, number.py, ocpp.py, repairs.py,
    select.py, sensor.py, services.yaml, strings.json, switch.py,
    webhook.py, translations/
```

## Configuration

**Settings → Devices & Services → Add Integration → Tap Electric Charger**.

### Basic mode setup

1. Create an API app at **web.tapelectric.app → Account → API management**.
2. Paste the `sk_...` primary API key.
3. (Optional) **Base URL**, **Charger ID** (to scope the integration
   to a single charger), **Webhook secret**.
4. When asked about advanced mode, choose **No** to finish with
   basic-only mode.

### Advanced mode setup (optional)

Advanced mode uses your regular Tap Electric app credentials (email +
password) to exchange for a Firebase refresh token. Tap sees this as
an app-style sign-in from Home Assistant.

- During initial setup: answer **Yes** to "Enable advanced mode?" and
  enter email + password.
- After setup: **Configure → Advanced mode → Enable**.

Your password is **never stored** — only the refresh token that Tap
issues is persisted in your config entry. If the refresh token is
later revoked, HA triggers a reauth flow and prompts for the password
once more.

### Options

**Configure → General settings** exposes:

| Option | Default | Range |
| --- | --- | --- |
| Active scan interval (s) | 30 | 10 – 300 |
| Idle scan interval (s) | 300 | 60 – 3600 |
| Session history limit | 50 | 10 – 500 |
| Meter data limit per session | 100 | 20 – 500 |
| Measurand stale threshold (min) | 15 | 5 – 120 |
| Energy decimals | 3 | 0 – 3 |
| Power decimals | 2 | 0 – 3 |
| Enable write operations | On | — |

Flipping **Enable write operations** off makes the whole integration
read-only. Pause/resume, limit, reset, and external meter push will
raise an HA Repairs issue instead of calling the API.

## Entity reference

Entities are created per charger. Names use HA's `has_entity_name`
convention — the device name stays "Tap Charger &lt;id prefix&gt;"
(or the charger's friendly name when available), and entities surface
as **Status**, **Charging**, **Plug connected**, etc.

| Entity | Platform | Default enabled | Data source | Notes |
| --- | --- | --- | --- | --- |
| Status | sensor | ✔ | public | Composite: connector wins over stale charger.status |
| Connector status | sensor | ✔ | public | Most-interesting-wins across connectors |
| Session energy | sensor | ✔ | public (fallback mgmt) | kWh; live during session |
| Session duration | sensor | ✔ | public | Minutes since startedAt |
| Last session energy | sensor | ✔ | **mgmt preferred**, public fallback | `source` attribute tells you which |
| Energy (active import register) | sensor | ✔ | public meter-data | OCPP measurand |
| Energy (short form) | sensor | ✔ | public meter-data | Some firmwares emit `Energy` instead of the long form |
| Energy interval / export, Power\*, Current\*, Voltage, SoC, Temperature, Frequency, Power factor | sensor | ✖ | public meter-data | See [charger compatibility](#hardware-compatibility) — enable per your hardware |
| Info | sensor | ✖ | public | Charger metadata (firmware, serial, partition) |
| Active tariff | sensor | ✖ | public | Only populated when Tap exposes a tariff for your scope |
| Current session energy | sensor | ✔ | mgmt | Advanced mode only |
| Current session duration | sensor | ✔ | mgmt | Advanced mode only |
| Current session started at | sensor | ✔ | mgmt | Advanced mode only |
| Current session driver | sensor | ✖ | mgmt | Advanced mode only, fleet installs |
| Current session location | sensor | ✖ | mgmt | Advanced mode only |
| Online | binary_sensor | ✔ | public | Connectivity to Tap cloud |
| Fault | binary_sensor | ✔ | public | Charger or connector fault |
| Plug connected | binary_sensor | ✔ | public | Per-connector |
| Charging | binary_sensor | ✔ | mgmt preferred | With `source` attribute |
| Charging allowed | switch | ✔ | public | Pause / resume via OCPP SetChargingProfile |
| Charge current limit | number | ✔ | public | Slider; persisted in entry.data |
| Auto-stop kWh / minutes / cost | number | ✖ | HA-local | Blueprint-driven thresholds |
| Reset | button | ✔ | public | OCPP Reset via dedicated endpoint |
| Reset type | select | ✖ | HA-local | Soft / Hard — preselects for the reset button |

## Hardware compatibility

This table is community-maintained. PRs welcome.

| Charger model | Confirmed features | Missing measurands | Notes |
| --- | --- | --- | --- |
| **EVBox Elvi** | status, sessions, Energy, remote reset | Power, Current, Voltage, SoC, Temperature | Firmware only emits `Energy` OCPP measurand. Default-disabled entities will stay Unavailable — leave them off unless you want them exposed for history. |
| Alfen | — (untested) | — | Expected full measurand set based on OCPP 1.6 spec. |
| Wallbox | — (untested) | — | |
| Zaptec | — (untested) | — | |

## Known limitations

- **EVBox Elvi publishes only the `Energy` OCPP measurand.** Power,
  Current, Voltage, SoC, and Temperature entities ship disabled-by-
  default; they activate automatically when the entity is enabled in
  the registry and a reading comes in.
- **`GET /charger-sessions/{id}` returns 404** on the public API
  (confirmed 2026-04-23 against api.tapelectric.app). Single-session
  detail is only available via the management API / advanced mode.
  See [`docs/API_INVENTORY.md`](docs/API_INVENTORY.md) for the full
  reverse-engineered map.
- **Orphan sessions** (`endedAt: null` on old rows) happen in ~22 %
  of captured history on the test account — cars get unplugged
  without a proper OCPP StopTransaction. The coordinator
  cross-checks connector status before treating a dangling session
  as live.
- `GET /tariffs` requires a `tariffId` query param and has no list
  form; the integration no longer polls it. Retail tariff data, when
  present, surfaces via the management API's session detail.
- Brand assets are intentionally absent — the integration shows the
  generic HA icon. See [`brands/README.md`](brands/README.md).

## Troubleshooting

1. Enable debug logging:
   ```yaml
   logger:
     default: info
     logs:
       custom_components.tapelectric: debug
   ```
2. Common pitfalls:
   - **401 repeatedly** → Your sk_ key was revoked. Regenerate it
     and reconfigure.
   - **Advanced-mode "degraded" log messages** → Management API is
     temporarily unreachable. Entities fall back to basic data. Will
     self-recover on the next successful tick.
   - **Entities Unavailable** → For measurands your charger doesn't
     emit, this is expected. Disable the entity or ignore.
   - **Writes failing with "writes disabled" Repairs issue** → Go
     back to **Configure → General → Enable write operations**.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for dev setup, test
commands, and PR guidelines.

The short version:

```bash
git clone https://github.com/weemaba999/homeassistant-tap-community
cd homeassistant-tap-community
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
pytest -q
```

## License

[MIT](LICENSE). See also the [acknowledgements](#acknowledgements)
below regarding third-party trademarks.

## Acknowledgements

- **Tap Electric B.V.** — for the public developer API. This
  integration talks to that API in good faith; the Tap name and
  logo are their property.
- **Home Assistant** — for the best open-source home automation
  platform on Earth.
- **Contributors** — listed in GitHub's contributors view. Thanks
  for every PR, bug report, and hardware-compatibility datapoint.
