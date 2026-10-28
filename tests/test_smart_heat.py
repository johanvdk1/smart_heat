"""Config flow, setup, sensors, learn_now, and the recorder -> heatmodel conversion."""

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
    assert "house.pump missing" in r["description_placeholders"]["problem"]


async def test_setup_sensors_and_learn_now(hass: HomeAssistant, caplog) -> None:
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_ZONES_FILE: ZONES_FILE})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.smart_heat_learning_run").state == "never"
    for z in ZONES:
        assert hass.states.get(f"sensor.smart_heat_{z}_learning") is not None
    # zone devices hang under the main device, linked by registry id (no deprecated via_device)
    from homeassistant.helpers import device_registry as dr
    reg = dr.async_get(hass)
    main = reg.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    zone_dev = reg.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_woonkamer"), entry.entry_id)
    assert main is not None and zone_dev.via_device_id == main.id
    assert "deprecated" not in caplog.text
    # empty recorder: the run must finish cleanly and process nothing
    await hass.services.async_call(DOMAIN, "learn_now", blocking=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    st = hass.states.get("sensor.smart_heat_learning_run")
    assert st.state == "ok", st.attributes
    assert st.attributes["days_processed"] == 0
    assert os.path.isdir(hass.config.path("smart_heat"))
    assert await hass.config_entries.async_unload(entry.entry_id)


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
