#!/usr/bin/env python3
"""Restrictia pe camere a unui cont: care camera cere o cerere HTTP?

Pana acum guard-ul verifica un singur lucru — `?channel=` — si orice ruta noua cu `cam=`
sau cu id-ul in cale ii scapa. Aici e un singur loc, pur si testabil, care recunoaste
TOATE formele: id in cale (/api/cam/<id>/..., /api/thumb/<id>, /api/cameras/<id>,
/api/harta/... nu — harta e filtrata in ruta), parametri (`channel`, `cam`, `camera`) si
campurile din corpul JSON. Guard-ul din server o intreaba pentru fiecare cerere a unui
cont restrans si raspunde 403 daca aceea camera nu e a lui.
"""
import re

CAI = (re.compile(r"^/api/cam/(\d+)(?:/|$)"), re.compile(r"^/api/thumb/(\d+)$"),
       re.compile(r"^/api/cameras/(\d+)(?:/|$)"), re.compile(r"^/api/v1/camera/(\d+)(?:/|$)"))
PARAMETRI = ("channel", "cam", "camera", "cam_id")


def cam_din_cerere(path, args=None, body=None):
    """Id-ul camerei la care se refera cererea, sau None daca nu numeste una."""
    for rx in CAI:
        m = rx.match(path or "")
        if m:
            return int(m.group(1))
    for sursa in (args or {}, body if isinstance(body, dict) else {}):
        for k in PARAMETRI:
            v = sursa.get(k)
            if v is None or v == "":
                continue
            try:
                return int(v)
            except (TypeError, ValueError):
                continue
    return None


def permisa(path, args, body, vizibile):
    """True daca un cont restrans la `vizibile` (multime de id-uri; None = toate) poate
    face cererea. O cerere care nu numeste nicio camera trece (rutele isi filtreaza
    singure listele)."""
    if vizibile is None:
        return True
    cid = cam_din_cerere(path, args, body)
    return cid is None or cid in vizibile
