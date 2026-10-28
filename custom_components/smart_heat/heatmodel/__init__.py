"""heatmodel: the zone heating model (air node, wall node, loss to outside, radiator lag, dead time).

Part of the Smart Heat integration. Pure Python +
numpy, no Home Assistant and no database imports, so the same code also runs outside Home Assistant
(for tests and offline analysis). Data comes in through a history callable:

    history(entity_id) -> iterable of (unix_ts, state_str, attributes_dict_or_None), oldest first

Modules: data (minute grid, zone data), model (step, simulate, forward), controller (valve model),
events (warm-ups, cool-downs, scoring), fit (parameter fit), lsq (bounded least squares in numpy;
no scipy, which Home Assistant does not ship).
"""
