# Klimaplan

Home-Assistant-Integration für Klimageräte im Sommer: **Bedienen** (Modus, Lüfter inkl. Auto, Soll) in einer eigenen Karte,
**Kühl-Zeitpläne je Raum** und **Hitzeschutz** – Schwester-Integration zu [Heizplan](https://github.com/PatrickHennigBerlin/ha-heizplan).

## Regeln

| | |
|---|---|
| **Hitzetag** | Außentemperatur über *Außengrenze* (Standard 30 °C). Endet erst unter Grenze − Hysterese (Standard 28 °C). |
| **Heute da** | Seit der Stichzeit (Standard 05:00) war jemand zu Hause → kein Urlaub. Optional zusätzlich ein Urlaubs-Schalter. |
| **Sperre** | Solange die Sperr-Entität an ist (z. B. `switch.heizperiode`), kühlt Klimaplan nicht automatisch. |
| **Hitzeschutz** | Hitzetag + heute da → Soll 25 °C, Lüfter Auto (Räume mit Hitzeschutz-Haken). Läuft auch weiter, wenn gerade niemand zu Hause ist. |
| **Zeitplan** | Hitzetag + heute da + *gerade jemand zu Hause* → Soll laut Kühlfenster (Standard 23 °C, Auto, Mo–Fr 18–21, Sa–So 10–21). |
| **Start** | Ein Gerät wird nur eingeschaltet, wenn es **aus** ist und **Ist ≥ Soll + 2 K** (Einschaltabstand). Dann: Kühlen, Soll aus Plan oder Hitzeschutz – der niedrigere gewinnt. |
| **Ende** | Gilt kein Soll mehr (Fenster vorbei, alle weg ohne Hitzeschutz, Hitzetag vorbei), schaltet Klimaplan **nur Geräte aus, die es selbst eingeschaltet hat**. |
| **Hand** | Ausschalten oder Moduswechsel von Hand (auch durch Skripte) pausiert den Raum bis zum nächsten Quellenwechsel (neues Fenster, Hitzeschutz ↔ Plan). Soll von Hand geändert → bleibt bis zum nächsten Wechsel. |

## Lüfter Auto

Hat das Gerät kein `fan_mode: auto` (z. B. KNX-Schnittstellen mit separatem Auto-Objekt), wird je Raum ein
`fan_auto_switch` hinterlegt. „Auto“ schaltet ihn ein, eine Stufe schaltet ihn aus und setzt `fan_mode`.

## Installation

HACS → Benutzerdefinierte Repositories → `PatrickHennigBerlin/ha-climate` (Integration) → installieren → Neustart →
Einstellungen → Geräte & Dienste → *Klimaplan* hinzufügen (Außentemperatur, Personen, optional Urlaub und Sperre).

Räume anlegen:

```yaml
action: klimaplan.add_room
data:
  climate: climate.klima_wohnzimmer
  name: Wohnzimmer
  fan_auto_switch: switch.lufter_auto_wohnzimmer
  plan_enabled: true
  protect: true
```

Karte:

```yaml
type: custom:klimaplan-card
title: Klima          # optional
rooms: [klima_kuche, klima_wohnzimmer, klima_schlafzimmer]   # optional Reihenfolge/Auswahl
view: rooms           # rooms | plan | auto
```

## Entitäten

- `switch.klimaplan_automatik`, `switch.klimaplan_zeitplane`, `switch.klimaplan_hitzeschutz`
- je Raum `switch.klimaplan_plan_<raum>`, `switch.klimaplan_hitzeschutz_<raum>`
- `number.klimaplan_aussengrenze`, `…_aussen_hysterese`, `…_einschaltabstand`, `…_hitzeschutz_soll`
- `binary_sensor.klimaplan_hitzetag`, `binary_sensor.klimaplan_anwesend_heute`, je Raum `binary_sensor.klimaplan_<raum>_kuhlt`
- je Raum `sensor.klimaplan_<raum>_kuhl_soll` (Attribut `quelle`: Zeitplan / Hitzeschutz / aus)

## Dienste

`klimaplan.add_room`, `klimaplan.update_room`, `klimaplan.set_day`, `klimaplan.set_settings` – siehe Entwicklerwerkzeuge → Aktionen.

## Entwicklung

```bash
pip install pytest-homeassistant-custom-component
pytest
```
