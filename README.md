# Tap Electric — Home Assistant custom integration

Niet-officiële integratie voor de [Tap Electric](https://developer.tapelectric.app/)
developer API. Ondersteunt:

- 🚗 **Start / stop laadsessies** (per charger, via switch-entity of HA service)
- 📊 **Live sessiedata** (kWh, vermogen, kost, duur) als sensor-entities
- 📜 **Historiek** van recente sessies (laatste 20) als attribuut
- 🔔 **Webhooks** met HMAC-SHA256 handtekeningverificatie (replay-protection)
- 🔌 **Per-charger device** in HA, meerdere chargers ondersteund

## ⚠️ Lees dit eerst

De `developer.tapelectric.app/reference` pagina wordt dynamisch gerenderd
met JavaScript. Ik kon de exacte endpoint-paden niet zien zonder API-key.
De integratie is gebouwd met de **meest waarschijnlijke** paden en header-
conventies. Na installatie:

1. Log in op [web.tapelectric.app](https://web.tapelectric.app) → Account →
   API management → maak een API app → noteer de primary key.
2. Open `https://developer.tapelectric.app/reference` met ingeplakte key
   (Scalar heeft typisch een "Authorize" knop bovenaan).
3. Controleer in de Reference de volgende zaken en pas aan in
   `custom_components/tapelectric/const.py` indien nodig:
   - **API host** (waarschijnlijk `https://api.tapelectric.app`, dit is
     een educated guess — de Reference "Try it" knoppen tonen de exacte host)
   - **Auth header**: `Authorization: Bearer <key>` vs `X-Api-Key` vs
     `X-Tap-Api-Key` → zet `AUTH_SCHEME` correct
   - **Endpoints** voor chargers, start, stop, sessions → zet de `PATH_*`
     constanten juist
4. Test met de standalone harness (buiten HA) om te valideren vóór install:
   ```bash
   cd custom_components
   TAP_API_KEY=tap_live_xxxxx python -m tapelectric.api
   ```

Als deze test werkt, ga door met de installatie hieronder.

## Installatie

### Via HACS (custom repo)
1. HACS → Integrations → ⋮ → Custom repositories
2. URL: `https://github.com/weemaba999/tapelectric-ha` (als je dit publiek
   zet via je RegimeLab org)
3. Category: Integration → Add
4. Installeer **Tap Electric** → herstart HA

### Handmatig
Kopieer de volledige folder `custom_components/tapelectric/` naar
`/config/custom_components/` op je HA VM en herstart HA.

```
/config/custom_components/tapelectric/
├── __init__.py
├── api.py
├── config_flow.py
├── const.py
├── coordinator.py
├── manifest.json
├── sensor.py
├── services.yaml
├── strings.json
├── switch.py
└── webhook.py
```

## Configuratie

Settings → Devices & Services → **Add Integration** → zoek "Tap Electric".

| Veld           | Uitleg                                                        |
|----------------|---------------------------------------------------------------|
| API key        | Primary API key uit het Tap dashboard                         |
| Base URL       | Standaard `https://api.tapelectric.app` — enkel wijzigen indien nodig |
| Charger ID     | Optioneel: beperk scope tot één charger (handig bij fleet)    |
| Webhook secret | Optioneel: activeert push-updates via webhooks                |

Webhook URL voor het Tap-dashboard: `https://<jouw-ha>.ui.nabu.casa/api/webhook/<webhook_id>`.
Het `webhook_id` verschijnt in `hass.data` en in de logs bij opstart.

## Services

### `tapelectric.start_charging`
```yaml
service: tapelectric.start_charging
data:
  charger_id: TAP-ABCDE-X
  connector_id: 1       # optioneel, default 1
  token_id: NL-TAP-C12345-6   # optioneel
```

### `tapelectric.stop_charging`
```yaml
service: tapelectric.stop_charging
data:
  charger_id: TAP-ABCDE-X
  session_id: sess_abc   # optioneel, anders actieve sessie
```

## Entities per charger

| Entity                                    | Type    | Beschrijving                       |
|-------------------------------------------|---------|------------------------------------|
| `switch.tap_charger_<naam>_charging`      | Switch  | On = charging, toggle start/stop   |
| `sensor.tap_charger_<naam>_status`        | Sensor  | Charger status (Available/Charging/…) |
| `sensor.tap_charger_<naam>_session_energy`| Energy  | kWh van actieve sessie             |
| `sensor.tap_charger_<naam>_session_power` | Power   | kW actueel                         |
| `sensor.tap_charger_<naam>_session_duration` | — | Minuten                            |
| `sensor.tap_charger_<naam>_session_cost`  | Sensor  | Kost actieve sessie                |
| `sensor.tap_charger_<naam>_last_session_energy` | Energy | kWh laatste afgeronde sessie |

## Webhook events

Bij een correct geconfigureerde webhook worden HA events gefired:

- `tapelectric_webhook` — alle events
- `tapelectric_sessionstarted`, `tapelectric_sessionended`,
  `tapelectric_sessionupdated`, `tapelectric_tokenauthorization`

Voorbeeld automation:

```yaml
alias: "Notify on EV charging start"
trigger:
  - platform: event
    event_type: tapelectric_sessionstarted
action:
  - service: notify.mobile_app
    data:
      message: "Sessie gestart op {{ trigger.event.data.chargerId }}"
```

## Dynamische tariffen (bonus)

Je hebt interesse in Charge Control via dynamische tarieven (zoals in de
Tap docs vermeld voor NL+BE). Overweeg een automation die de sessie
pauzeert als je energy-price hoog staat:

```yaml
trigger:
  - platform: numeric_state
    entity_id: sensor.entsoe_current_price
    above: 0.35
condition:
  - condition: state
    entity_id: switch.tap_charger_thuis_charging
    state: "on"
action:
  - service: tapelectric.stop_charging
    data:
      charger_id: "{{ state_attr('switch.tap_charger_thuis_charging', 'charger_id') }}"
```

## Troubleshooting

- **401 bij eerste poll** → `AUTH_SCHEME` staat verkeerd. Probeer in
  `const.py`: `"bearer"` → `"x-api-key"` → `"x-tap-api-key"`.
- **404 op `/chargers`** → endpoint path klopt niet. Check in Reference
  of ze `/stations`, `/charge-points`, etc. gebruiken en pas
  `PATH_CHARGERS_LIST` aan.
- **Sensors blijven leeg** → de veldnamen in de JSON response matchen
  niet. Activeer debug logging:
  ```yaml
  logger:
    logs:
      custom_components.tapelectric: debug
  ```
  Copy-paste een response uit `custom_components.tapelectric.api` DEBUG
  en stuur door — dan kan de sensor-mapping aangepast worden.

## Niet bevestigd in de docs

Deze items konden niet uit de publieke docs gehaald worden en zijn
inschattingen op basis van standaard EV-API conventies:

| Item | Status |
|------|--------|
| API host | Onbevestigd (`api.tapelectric.app` is een inschatting) |
| Auth header | Onbevestigd (`Bearer` is de inschatting) |
| Endpoint paths | Onbevestigd (standaard REST/OCPP conventies) |
| Query param namen (from/to/limit) | Onbevestigd |
| Response veldnamen (`chargerId` vs `charger_id` vs `id`) | De code probeert meerdere varianten |
| HMAC webhook verificatie | ✅ **Bevestigd** uit de Basics-pagina van de docs |
| Webhook event schema | Deels bevestigd (`TokenAuthorization` voorbeeld), de rest ingeschat |
