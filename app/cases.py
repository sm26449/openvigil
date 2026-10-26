#!/usr/bin/env python3
"""Pachetul de caz — un dosar de probe pentru un incident, gata de predat.

Cand un operator vede ceva grav, aduna mai multe momente (interval pe o camera)
intr-un singur "caz". La final exporta un ZIP: fiecare clip cu watermark (camera +
ora gravate in imagine), plus manifest.json cu descrierea si suma SHA-256 a fiecarui
fisier — asa dosarul e verificabil si nu poate fi umblat pe ascuns.

Aici e doar metadata (data/cases.json): CRUD-ul cazurilor si al probelor. Randarea
clipurilor si impachetarea ZIP le face server.py (are acces la ffmpeg si la NVR).
"""
import json
import os
import threading
import time
import uuid

from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_FILE = os.path.join(ROOT, "data", "cases.json")
_lock = threading.Lock()


def _load():
    try:
        with open(CASES_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save(data):
    with _lock:
        os.makedirs(os.path.dirname(CASES_FILE), exist_ok=True)
        tmp = CASES_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CASES_FILE)


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def create(titlu, who=None, descriere=""):
    """Deschide un caz nou. Returneaza cazul creat."""
    titlu = (titlu or "").strip()
    if not titlu:
        raise ValueError(t("titlul cazului e obligatoriu"))
    data = _load()
    cid = uuid.uuid4().hex[:12]
    data[cid] = {"id": cid, "titlu": titlu, "descriere": (descriere or "").strip(),
                 "creat_de": who, "creat": _now(), "probe": []}
    _save(data)
    return data[cid]


def lista():
    """Toate cazurile, cel mai nou primul, cu numarul de probe."""
    data = _load()
    out = [{**c, "nr_probe": len(c.get("probe", []))} for c in data.values()]
    # cel mai nou primul; la timestamp egal (aceeasi secunda) pastreaza ordinea
    # inversa de creare — dict-ul retine ordinea inserarii
    out.sort(key=lambda c: c.get("creat", ""))
    out.reverse()
    return out


def get(cid):
    return _load().get(cid)


def delete(cid):
    data = _load()
    existed = data.pop(cid, None) is not None
    if existed:
        _save(data)
    return existed


def add_proba(cid, cam, start, end, nota="", event_id=None, who=None):
    """Adauga o proba (interval pe o camera) la caz. start/end = timestamp Hik (…Z)."""
    data = _load()
    c = data.get(cid)
    if not c:
        raise KeyError("caz inexistent")
    pid = uuid.uuid4().hex[:8]
    c["probe"].append({"id": pid, "cam": int(cam), "start": start, "end": end,
                       "nota": (nota or "").strip(), "event_id": event_id,
                       "adaugat_de": who, "adaugat": _now()})
    _save(data)
    return c


def del_proba(cid, pid):
    data = _load()
    c = data.get(cid)
    if not c:
        raise KeyError("caz inexistent")
    n = len(c["probe"])
    c["probe"] = [p for p in c["probe"] if p.get("id") != pid]
    changed = len(c["probe"]) != n
    if changed:
        _save(data)
    return changed
