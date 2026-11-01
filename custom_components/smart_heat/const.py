"""Constants for Smart Heat."""

from datetime import timedelta

DOMAIN = "smart_heat"
CONF_ZONES_FILE = "zones_file"
DEFAULT_ZONES_FILE = "heating/zones.yaml"  # the house description, relative to /config; not part of the integration
CONF_HEATING_DIR = "heating_dir"  # 0.1.0 entries: folder holding zones.yaml
OUTPUT_DIR = "smart_heat"  # /config/smart_heat: warmups.csv and predictions.csv, readable outside HA
PLAN_INTERVAL = timedelta(minutes=1)
RECORD_INTERVAL = timedelta(minutes=15)
CARDS_URL = "/smart_heat/smart-heat-cards.js"
SIGNAL_UPDATED = f"{DOMAIN}_updated"
