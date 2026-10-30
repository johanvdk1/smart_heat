# Smart Heat

Home Assistant integration (domain `smart_heat`): a smart thermostat per heating zone that learns how
each zone heats up and cools down, and starts the heating so a zone reaches its target on time.
Private, for one house. 

## Status

Version 0.4.0 — **read-only**: the nightly learning routine of the learning
phase runs in HA. Nothing is switched.

- Nightly at 06:30, and on the action `smart_heat.learn_now`: per zone, every finished day not yet in
  the log is fitted on the earlier days and predicted, predicted against actual is logged.
- `sensor.smart_heat_learning_run`: never / running / ok / error.
- `sensor.smart_heat_<zone>_learning`: the last learned day, with rmse, parameters, jumps, checks and that
  day's warm-ups and cool-downs.
- Checks (0.4.0): every fit is checked for plausibility (`heatmodel/checks.py`): `unreliable` on a loss or
  heating rate fitted to 0, or night cooling more than 0.5 K off (median of at least 5 closed-valve
  nights); warnings for a model simpler than designed (no wall node, no radiator lag); the experience
  (coldest outdoor, largest gap of the warm-ups trained on). Each warm-up is marked `outside` when it
  lies beyond that experience. Nothing acts on it yet.
- Learning log: `/config/smart_heat/predictions.csv` and `params.csv` (outside git).

## What lives where

Everything the integration needs is in this repo, the model package included
(`custom_components/smart_heat/heatmodel/`: data, model, controller, events, fit, checks, learning;
numpy only, which ships with HA). The house is described in `zones.yaml`, which is not part of
this repo: it is specific to the house, written and changed by hand, and kept in the HA config
(default `/config/heating/zones.yaml`, asked in the config flow). It is validated on load, with a
message per missing key.

## Install

Through HACS (custom repository):

1. HACS → ⋮ → Custom repositories → `https://github.com/johanvdk1/smart_heat`, type Integration.
2. Download Smart Heat, restart Home Assistant.
3. Settings → Devices & services → Add integration → Smart Heat, zones file (default
   `heating/zones.yaml`, relative to the configuration directory).

Requires Home Assistant 2026.10 or later.

## Tests

With HA 2026.10.4 (`pytest-homeassistant-custom-component==0.13.375`):

    uv venv && uv pip install "pytest-homeassistant-custom-component==0.13.375" pyyaml
    .venv/bin/python -m pytest tests

`SMART_HEAT_ZONES_FILE` points the tests at a house description (default `~/ha/staging/heating/zones.yaml`).

## License

GPL-3.0, see `LICENSE`.
