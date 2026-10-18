"""Konstanten für Klimaplan."""

DOMAIN = "klimaplan"
VERSION = "0.1.1"

STORAGE_KEY = "klimaplan"
STORAGE_VERSION = 1

CARD_URL = "/klimaplan_static/klimaplan-card.js"

SIGNAL_UPDATED = "klimaplan_updated"

# Optionen (config entry options)
CONF_OUTSIDE = "outside_entity"        # Außentemperatur (sensor, °C)
CONF_PERSONS = "persons"               # person.* für Anwesenheit
CONF_VACATION = "vacation_entity"      # optional: an = Urlaub (input_boolean/switch/binary_sensor)
CONF_BLOCK = "block_entity"            # optional: an = Kühlen gesperrt (z. B. Heizperiode)

SOURCE_PLAN = "plan"
SOURCE_PROTECT = "protect"
