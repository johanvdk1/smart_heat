"""zones.yaml: validation and the entities a zone needs (used by shadow.py and Smart Heat)."""

EVENT_KEYS = ('warm_step', 'warm_gap', 'target_window_min', 'reached_tol', 'warm_window_min', 'cool_step',
              'cool_gap', 'coast_end_rise', 'coast_window_min', 'min_spacing_min')


def validate(conf):
    """List of problems in a parsed zones.yaml (empty = usable)."""
    err = []
    if not isinstance(conf, dict): return ['zones.yaml is not a mapping']
    H, D, Z = conf.get('house') or {}, conf.get('data') or {}, conf.get('zones') or {}
    for k in ('pump', 'outdoor'):
        if not H.get(k): err.append(f'house.{k} missing')
    if not D.get('start'): err.append('data.start missing')
    ev = D.get('events') or {}
    err += [f'data.events.{k} missing' for k in EVENT_KEYS if k not in ev]
    if not Z: err.append('no zones')
    for zn, zc in Z.items():
        rooms = (zc or {}).get('rooms') or {}
        if not rooms: err.append(f'zone {zn}: no rooms'); continue
        for rn, rc in rooms.items():
            for k in ('temperature', 'valve', 'climate'):
                if not (rc or {}).get(k): err.append(f'zone {zn}, room {rn}: {k} missing')
        if len(rooms) > 1 and zc.get('reference_room') not in rooms:
            err.append(f'zone {zn}: several rooms, reference_room missing or not one of them')
        if 'worst_late_min' not in zc and 'lead_time_min' not in zc:
            err.append(f'zone {zn}: worst_late_min or lead_time_min missing')
        if 'comfort_floor' not in zc: err.append(f'zone {zn}: comfort_floor missing')
    return err


def entities(conf, zone):
    """Every entity whose history ZoneData reads for this zone."""
    H, Z = conf['house'], conf['zones'][zone]
    out = [H['pump'], H['outdoor']] + ([H['outdoor_internet']] if H.get('outdoor_internet') else [])
    for rc in Z['rooms'].values():
        out += [rc['temperature'], rc['valve'], rc['climate']] + ([rc['door']] if rc.get('door') else [])
    out += list(Z.get('in_use') or [])
    nbs = Z.get('neighbours') or []
    if nbs: out.append(nbs[0]['temperature'])
    return list(dict.fromkeys(out))
