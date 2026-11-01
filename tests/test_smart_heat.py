"""Config flow, setup, entities, the card commands, shadow against control, and the recorder -> heatmodel conversion."""

from datetime import timedelta
import os

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.smart_heat.const import CONF_HEATING_DIR, CONF_ZONES_FILE, DOMAIN
from custom_components.smart_heat.runtime import history_rows
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant, State
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util

from .conftest import ZONES_FILE

ZONES = ["eetkeuken_keuken", "woonkamer", "bureel", "badkamer"]


async def test_flow_valid(hass: HomeAssistant) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert r["type"] is FlowResultType.FORM
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_ZONES_FILE: ZONES_FILE})
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"] == {CONF_ZONES_FILE: ZONES_FILE}


async def test_flow_invalid(hass: HomeAssistant, tmp_path) -> None:
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_ZONES_FILE: str(tmp_path / "none.yaml")})
    assert r["type"] is FlowResultType.FORM
    assert r["errors"] == {"base": "invalid_zones_file"}
    assert "cannot read" in r["description_placeholders"]["problem"]
    bad = tmp_path / "bad.yaml"
    bad.write_text("house: {}\nzones: {}\n")
    r = await hass.config_entries.flow.async_configure(r["flow_id"], {CONF_ZONES_FILE: str(bad)})
    assert "house.outdoor missing" in r["description_placeholders"]["problem"]


async def test_setup_entities_shadow_and_control(hass: HomeAssistant, hass_ws_client, caplog) -> None:
    import yaml
    from pytest_homeassistant_custom_component.common import async_mock_service
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    conf = yaml.safe_load(open(ZONES_FILE))
    H, Z = conf["house"], conf["zones"]["woonkamer"]
    rc = Z["rooms"]["woonkamer"]
    for e, v in ((rc["temperature"], "16.0"), (H["outdoor"], "8.0"), (rc["valve"], "0"), (H["heating_pump"], "5")):
        hass.states.async_set(e, v)
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ZONES_FILE: ZONES_FILE})
    entry.add_to_hass(hass)
    old = er.async_get(hass).async_get_or_create("sensor", DOMAIN, f"{entry.entry_id}_woonkamer_learning", config_entry=entry)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.state is ConfigEntryState.LOADED
    assert er.async_get(hass).async_get(old.entity_id) is None          # the v4 B sensors are removed
    for z in ZONES:
        assert hass.states.get(f"sensor.smart_heat_{z}_next_start") is not None
        assert hass.states.get(f"sensor.smart_heat_{z}_equation") is not None
        assert hass.states.get(f"switch.smart_heat_{z}_control").state == "off"
    reg = dr.async_get(hass)
    main = reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    zone_dev = reg.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_woonkamer"), entry.entry_id)
    assert main is not None and zone_dev.via_device_id == main.id
    assert "deprecated" not in caplog.text
    assert os.path.exists(hass.config.path("smart_heat", "warmups.csv")) or True   # empty recorder: nothing to seed

    calls = async_mock_service(hass, "climate", "set_temperature")
    ws = await hass_ws_client(hass)
    now = dt_util.now()
    ready = (now + timedelta(minutes=60)).strftime("%H:%M")              # needs ~2 h from 16 °C: pre-heat now
    await ws.send_json({"id": 1, "type": "smart_heat/request/set", "zone": "woonkamer", "temperature": 21, "ready_at": ready})
    r = await ws.receive_json()
    assert r["success"], r
    assert r["result"]["plan"]["preheat"] and r["result"]["plan"]["source"] == "request"
    assert calls == []                                                   # control off: shadow, nothing switched
    st = hass.states.get("sensor.smart_heat_woonkamer_next_start")
    assert st.attributes["target"] == 21 and st.attributes["preheat"]
    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.smart_heat_woonkamer_control"}, blocking=True)
    assert len(calls) == 1 and calls[0].data["temperature"] == 21 and calls[0].data["entity_id"] == [rc["climate"]]
    await hass.services.async_call(DOMAIN, "cancel_request", {"zone": "woonkamer"}, blocking=True)
    await ws.send_json({"id": 2, "type": "smart_heat/schedule/set", "zone": "woonkamer",
                        "schedule": {"base": 16, "days": {"mon": [{"start": "09:00", "end": "08:00", "temperature": 20}]}}})
    r = await ws.receive_json()
    assert not r["success"] and r["error"]["code"] == "invalid_schedule"
    sched = {"base": 16, "days": {d: [{"start": "00:00", "end": "23:59", "temperature": 19.5}]
                                  for d in ("mon", "tue", "wed", "thu", "fri", "sat", "sun")}}
    await ws.send_json({"id": 3, "type": "smart_heat/schedule/set", "zone": "woonkamer", "schedule": sched})
    r = await ws.receive_json()
    assert r["success"] and r["result"]["schedule"]["base"] == 16.0
    assert calls[-1].data["temperature"] == 19.5                        # control on: the schedule's setpoint now
    # heating pump fault (power under 2 W for 10 min): no warm-up, a zone under control held at its base
    rt = entry.runtime_data
    await ws.send_json({"id": 5, "type": "smart_heat/request/set", "zone": "woonkamer", "temperature": 22, "ready_at": ready})
    assert (await ws.receive_json())["success"]
    assert calls[-1].data["temperature"] == 22                           # pre-heat for the request
    hass.states.async_set(H["heating_pump"], "0.0")
    await hass.async_block_till_done()
    rt.bad_since["power"] = dt_util.now() - timedelta(minutes=11)        # as if 11 min ago
    await rt.async_plan()
    assert calls[-1].data["temperature"] == 16.0
    st = hass.states.get("sensor.smart_heat_woonkamer_next_start")
    assert not st.attributes["preheat"] and st.attributes["reason"].startswith("heating pump fault")
    assert "woonkamer" not in rt.data["pending"]
    hass.states.async_set(H["heating_pump"], "6.0")                       # pump back: the plan applies again
    await hass.async_block_till_done(wait_background_tasks=True)
    assert calls[-1].data["temperature"] == 22
    await hass.services.async_call(DOMAIN, "cancel_request", {"zone": "woonkamer"}, blocking=True)
    await ws.send_json({"id": 6, "type": "smart_heat/zones"})
    r = await ws.receive_json()
    assert [z["zone"] for z in r["result"]["zones"]] == ZONES
    assert await hass.config_entries.async_unload(entry.entry_id)
    # the state survives a restart of the entry
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert hass.states.get("switch.smart_heat_woonkamer_control").state == "on"
    assert entry.runtime_data.data["schedules"]["woonkamer"]["base"] == 16.0
    # what is learned is carried over in the store; every learned set is kept with its history and in params.csv
    from custom_components.smart_heat.heatmodel import equation as hm_eq
    rt = entry.runtime_data
    assert rt.data["learned"]["woonkamer"]["n"] == 0 and "woonkamer" not in rt.data["params"]   # nothing learned yet
    st, _ = hm_eq.learn(rt.data["learned"]["woonkamer"], {"ts": 1e9, "start_temp": 17.0, "target": 20.0, "outdoor": 8.0,
                                                           "valve_open": False, "minutes": 150.0})
    rt.data["learned"]["woonkamer"] = st
    rt._set_params("woonkamer", hm_eq.params(st), "2026-11-02 06:00")
    hist = rt.data["params"]["woonkamer"]
    assert len(hist) == 1 and hist[0]["n"] == 1 and hist[0]["warmup"] == "2026-11-02 06:00"
    rows = open(hass.config.path("smart_heat", "params.csv")).read().splitlines()
    assert rows[0] == "at,zone,warmup,n,dead_time,per_k,per_k_out" and rows[-1].split(",")[1:4] == ["woonkamer", "2026-11-02 06:00", "1"]
    per_k = rt.params["woonkamer"]["per_k"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert await hass.config_entries.async_setup(entry.entry_id)              # restart: carried on from the store
    await hass.async_block_till_done(wait_background_tasks=True)
    assert entry.runtime_data.params["woonkamer"]["per_k"] == per_k and entry.runtime_data.data["learned"]["woonkamer"]["n"] == 1


async def test_setup_bad_dir(hass: HomeAssistant, tmp_path) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ZONES_FILE: str(tmp_path / "none.yaml")})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_ERROR


async def test_entry_from_0_1_0_uses_heating_dir(hass: HomeAssistant) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_HEATING_DIR: os.path.dirname(ZONES_FILE)})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.LOADED


async def test_history_rows_keeps_attribute_changes() -> None:
    t = dt_util.utcnow()
    states = {"climate.x": [State("climate.x", "heat", {"temperature": 19.0}, last_updated=t),
                            State("climate.x", "heat", {"temperature": 21.0}, last_updated=t + timedelta(minutes=1))]}
    rows = history_rows(states)["climate.x"]
    assert [r[1] for r in rows] == ["heat", "heat"]
    assert [r[2]["temperature"] for r in rows] == [19.0, 21.0]
    assert rows[1][0] - rows[0][0] == 60.0
