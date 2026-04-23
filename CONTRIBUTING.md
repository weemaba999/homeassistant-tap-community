# Contributing to homeassistant-tap-community

Thanks for considering a contribution! This project is a community
integration — every bug report, PR, and hardware compatibility
datapoint helps.

## Dev environment

```bash
git clone https://github.com/weemaba999/homeassistant-tap-community
cd homeassistant-tap-community
python -m venv .venv && source .venv/bin/activate
pip install -r requirements_test.txt
```

That pulls pytest, aioresponses, pytest-homeassistant-custom-component,
and coverage. Python 3.12 is the target.

## Running the tests

```bash
# Full suite + coverage
pytest --cov=custom_components/tapelectric --cov-report=term-missing

# Quick loop while iterating
pytest -q

# Single file / single test
pytest tests/test_api.py -q
pytest tests/test_coordinator.py::test_update_data_populates_chargers_and_sessions -q
```

Tests fall into two categories:

1. **Standalone** — run anywhere, no HA install required. Covers
   `api`, `api_management`, `auth_firebase`, `ocpp`, `coordinator`,
   `sensor`, `binary_sensor`, `switch`, `number`, `button`,
   `select`, migration, reauth trigger.
2. **`@pytest.mark.requires_ha`** — need `homeassistant` and
   `pytest-homeassistant-custom-component` installed. Covers the
   real config/options flow round-trips. Auto-skipped without HA.

The CI workflow (`.github/workflows/validate.yml`) installs
everything so both categories run on every push / PR.

## Live API smoke tests

Optional — only if you have a Tap sk_ key and want to poke the real
API:

```bash
# Put your key in .tap.env (already gitignored):
# TAP_API_KEY=sk_...

source .tap.env
python3 tests/probe_api_inventory.py          # both sides
SKIP_MGMT=1 python3 tests/probe_api_inventory.py  # public-only
```

Probes are **read-only**. The `/chargers/{id}/reset` endpoint is
deliberately catalogued but never invoked.

## Coding conventions

- Python 3.12, `from __future__ import annotations`.
- Lean on Home Assistant's built-in helpers: `DataUpdateCoordinator`,
  `ConfigEntry`, `HomeAssistantError`, Repairs. Don't hand-roll what
  HA already provides.
- Unique IDs are **frozen** once released. If you need to change a
  sensor's shape, add a new unique_id; don't silently rotate the old
  one (HA will orphan the old entity and break historical data).
- Translations: add keys to `strings.json` first, then mirror to
  `translations/{en,nl,de,fr}.json`. NL is Belgian Dutch — prefer
  "laadsessie", "laadpaal", "chauffeur".
- Entity registry defaults: when in doubt, default-disable. It's
  better for users to toggle something on than to have a cluttered
  device page on day one.

## PR checklist

Before opening a PR:

- [ ] `pytest -q` passes locally.
- [ ] New behaviour has a new test (not just the happy path —
  include one failure-mode test).
- [ ] Unique IDs are stable (or a migration story is documented).
- [ ] Translations: if you added / changed a `strings.json` key, the
  four language files are also updated (or at least `en.json` and
  `nl.json`; DE / FR can stay machine-translated with a note in the
  CHANGELOG).
- [ ] `CHANGELOG.md` has an "Unreleased" entry describing the
  change.
- [ ] For write-path changes: confirmed write still respects
  `write_enabled=False` and the Repairs issue fires.
- [ ] No brand assets (PNGs, etc.) — see [`brands/README.md`](brands/README.md).

## Issue reports

Include:

- HA Core version, this integration's version.
- Charger model + firmware (from the Info sensor).
- Relevant log lines with `custom_components.tapelectric: debug`.
- Whether advanced mode is enabled.

## Licensing

By contributing you agree your contributions are licensed under the
project's [MIT](LICENSE) license.
