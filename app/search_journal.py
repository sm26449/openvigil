"""Observatii din jurnal pentru intrebari: filtre in SQL, fara LIMIT global inaintea filtrarii."""
import json
import time
from collections import Counter

import db
import jurnal
import search_time


def find(label, since, until, visible=None, hours=None, first=False):
    aliases = {
        'car': ['car', 'masina', 'mașină'], 'person': ['person', 'om', 'persoana', 'persoană'],
        'truck': ['truck', 'camion'], 'dog': ['dog', 'caine', 'câine'],
        'cat': ['cat', 'pisica', 'pisică'], 'bird': ['bird', 'pasare', 'pasăre'],
        'chicken': ['chicken', 'gaina', 'găină'], 'pigeon': ['pigeon', 'porumbel'],
    }.get(label, [label])
    conditions = ["tip IN ('obiect', 'detectie')", 'ts >= %s', 'ts < %s',
                  "date::jsonb->>'eticheta' = ANY(%s)"]
    args = [since, until, aliases]
    if visible is not None:
        conditions.append('cam = ANY(%s)')
        args.append(sorted(visible))
    jurnal.deschide()
    groups, active, directions = [], {}, Counter()
    with db.conn() as c:
        with c.transaction(), c.cursor(name='search_observations') as cursor:
            cursor.execute('SELECT id, t, ts, cam, tip, date FROM events WHERE '
                           + ' AND '.join(conditions) + ' ORDER BY ts, seq', args)
            for id_, stamp, ts, cam, kind, raw in cursor:
                if cam is None or not search_time.in_hours(stamp[11:], hours):
                    continue
                e = json.loads(raw)
                if kind == 'obiect' and e.get('directie') and e['directie'] != '?':
                    directions[e['directie']] += 1
                g = active.get(cam) if kind == 'detectie' else None
                if g and ts-g['_last'] <= 300 and ts-g['_start'] < 1800:
                    g['_last'] = ts
                    g['sfarsit'] = stamp
                    g['poze'] += 1
                    continue
                g = {'inceput': stamp, 'sfarsit': stamp, 'camere': [cam], 'poze': 1,
                     'scor': 1., 'coperta': None, 'cover_cam': cam, 'cover_t': stamp,
                     'source': kind, 'event_id': id_, '_last': ts, '_start': ts,
                     'durata': e.get('durata', 0)}
                groups.append(g)
                if kind == 'detectie':
                    active[cam] = g
    for g in groups:
        g.pop('_last')
        g.pop('_start')
    groups.sort(key=lambda g: g['inceput'], reverse=not first)
    return {'groups': groups[:20], 'tracked': sum(g['source'] == 'obiect' for g in groups),
            'episodes': sum(g['source'] == 'detectie' for g in groups),
            'directions': dict(directions), 'complete': False}
