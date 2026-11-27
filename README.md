# Smart Heat

Home Assistant integration (domain `smart_heat`): a smart thermostat per heating zone that learns how
each zone heats up and cools down, and starts the heating so a zone reaches its target on time.
Private, for one house. 

## Status

Version 0.5.2 — the start time comes from the owner's equation per zone (design decision 28):

    minutes = dead time − valve-open saving (only when the valve is already open)
              + minutes per K × (target − room) + minutes per K outside × (target − outdoor)

The valve-open saving is learned per zone, starting at 0 (0.5.0 halved the dead time instead); it is never
more than the dead time.

- **Learning, after every warm-up**: 30 min after the reference room reached its target (within
  `reached_tol`, 0.3 K) the warm-up is written to `/config/smart_heat/warmups.csv`; the error of the
  zone's current numbers on it corrects them, and the corrected numbers carry over to the next warm-up
  (recursive least squares: a number moves more the more the warm-up says about it and the less certain it
  still is; every warm-up counts equally, however old; nothing is recalculated). Limits: dead time 0–90 min,
  2–90 min per K, 0–5 min per K outside, valve-open saving 0–90 min. Not learned from: interrupted, not reached within 5 h, door open,
  heating pump fault, outside the working range (outdoor −15 to 21 °C, inside 5 to 23 °C). On the first
  start every warm-up since `data.start` is recorded from the recorder and learned in order. Every learned set
  is kept with its version history in Smart Heat's store (`.storage/smart_heat.state`) and copied as a row
  to `/config/smart_heat/params.csv`; at a start the numbers come from the store. A zone whose stored
  numbers belong to an earlier version of the equation is learned again from `warmups.csv`, in order, from
  the starting numbers (the earlier sets stay in the history).
- **Schedule** per zone, Smart Heat's own (a base temperature and periods per weekday), and one-off
  **requests** ("21 °C at 19:00"). They only say where the targets are ("X °C by HH:MM").
- **Planner**, every minute: from the measured room and outdoor temperature, the setpoint goes to the
  coming target when now + minutes reaches the desired time; at most 4 h early; aim 0 min late.
  Each decision and its result go to `/config/smart_heat/predictions.csv`.
- **Control** per zone (`switch.smart_heat_<zone>_control`), **off by default**: off = shadow, the plan is
  only logged; on = Smart Heat sets the target on the zone's thermostats at the pre-heat start, once per
  pre-heat (a manual change after it stands). **Smart Heat only raises**: it never sends anything else, not
  the base at a period's end, not at switching control on unless a pre-heat is due, and not during a heating
  pump fault (then no pre-heat starts). Lowering is left to the thermostats and the house's own automations.
- Entities per zone: `sensor.smart_heat_<zone>_next_start` (planned start, with target, ready time,
  minutes, reason), `sensor.smart_heat_<zone>_equation` (warm-ups learned, the four numbers, the last
  warm-up), the control switch.
- Actions: `smart_heat.request` (zone, temperature, ready_at `HH:MM` or a date and time),
  `smart_heat.cancel_request`.
- v4 B (the physics model, `heatmodel/` data … checks) is no longer run; its code is kept for coasting
  later. Its old log is renamed once to `v4b_predictions.csv` / `v4b_params.csv`.

## Cards

Served by the integration itself (no resource to add):

    type: custom:smart-heat-thermostat-card   # temperature, setpoint, plan, control; "21 °C by 19:00"
    zone: woonkamer

    type: custom:smart-heat-schedule-card     # the zone's week: base temperature, periods per day
    zone: woonkamer

## What lives where

Everything the integration needs is in this repo, the model package included
(`custom_components/smart_heat/heatmodel/`: equation, warmups, schedule; and v4 B's data, model, controller, events, fit, checks, learning;
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
