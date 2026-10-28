"""Constants for Smart Heat."""

DOMAIN = "smart_heat"
CONF_ZONES_FILE = "zones_file"
DEFAULT_ZONES_FILE = "heating/zones.yaml"  # the house description, relative to /config (decision 23)
CONF_HEATING_DIR = "heating_dir"  # 0.1.0 entries: folder holding zones.yaml
OUTPUT_DIR = "smart_heat"  # /config/smart_heat: learning log, outside git (decision 23)
LEARN_TIME = (6, 30)  # nightly learning run; a day is ready 6 h after midnight (coast window)
SIGNAL_UPDATED = f"{DOMAIN}_updated"
