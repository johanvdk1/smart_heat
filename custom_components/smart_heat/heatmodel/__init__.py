"""heatmodel: the frozen v4 B zone heating model (AUTO_HEAT_DESIGN.md, decisions 10, 15, 22).

Part of the Smart Heat integration (custom_components/smart_heat/heatmodel, decision 23). Pure Python +
numpy, no Home Assistant and no database imports, so the integration and the Mac tools (shadow.py,
lodo_c.py, which import this folder) run the same code. Data comes in through a history callable:

    history(entity_id) -> iterable of (unix_ts, state_str, attributes_dict_or_None), oldest first

Modules: data (minute grid, zone data), model (step, simulate, forward), controller (valve model),
events (warm-ups, cool-downs, scoring), fit (parameter fit), lsq (bounded least squares in numpy;
no scipy, which Home Assistant does not ship).
"""
