#!/usr/bin/env python3
"""Harta proprietatii: un plan (imagine) cu camerele asezate si conurile lor de vedere.

Structura permite mai multe planuri (etaje, curte/casa), fiecare cu propria imagine si
propriile pozitii; interfata arata selectorul doar cand sunt mai multe. Pozitiile sunt
fractii din imagine (0..1), deci planul se poate reincarca la alta rezolutie fara sa
se piarda nimic; `dir` = directia privirii in grade (0 = sus, in sensul ceasului),
`fov` = deschiderea conului in grade.

  data/harta.json         {"planuri": [{"id", "nume", "imagine": true/false,
                                        "camere": {"<cam>": {"x","y","dir","fov"}}}]}
  data/harta/<id>.<ext>   imaginea planului (png/jpg/webp), incarcata din interfata
"""
import os
import re
import threading
import uuid

import store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "data", "harta.json")
DIR = os.path.join(ROOT, "data", "harta")
MAX_IMG = 8 * 1024 * 1024
TIPURI = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_lock = threading.Lock()
_ID_RE = re.compile(r"^[a-z0-9]{4,16}$")


def _load():
    d = store.load_json(FILE, None) or {}
    pl = [p for p in (d.get("planuri") or []) if isinstance(p, dict) and _ID_RE.match(str(p.get("id", "")))]
    return {"planuri": pl}


def _save(d):
    store.atomic_write_json(FILE, d, mode=0o600, indent=2)


def _imagine(pid):
    """Calea imaginii planului (orice extensie permisa) sau None."""
    if not _ID_RE.match(pid):
        return None
    for ext in TIPURI.values():
        p = os.path.join(DIR, f"{pid}.{ext}")
        if os.path.exists(p):
            return p
    return None


def lista(visible=None):
    """Planurile cu camerele lor; un cont restrans vede doar camerele lui pe plan."""
    out = []
    for p in _load()["planuri"]:
        cams = {k: v for k, v in (p.get("camere") or {}).items()
                if visible is None or int(k) in visible}
        out.append({"id": p["id"], "nume": p.get("nume") or "Plan",
                    "imagine": _imagine(p["id"]) is not None, "camere": cams})
    return out


def plan_nou(nume):
    nume = (nume or "").strip()[:40] or "Plan"
    with _lock:
        d = _load()
        p = {"id": uuid.uuid4().hex[:8], "nume": nume, "camere": {}}
        d["planuri"].append(p)
        _save(d)
    return p


def redenumeste(pid, nume):
    with _lock:
        d = _load()
        p = next((x for x in d["planuri"] if x["id"] == pid), None)
        if not p:
            raise ValueError("planul nu există")
        p["nume"] = (nume or "").strip()[:40] or p.get("nume") or "Plan"
        _save(d)
    return p


def sterge(pid):
    with _lock:
        d = _load()
        rest = [x for x in d["planuri"] if x["id"] != pid]
        if len(rest) == len(d["planuri"]):
            raise ValueError("planul nu există")
        d["planuri"] = rest
        _save(d)
    img = _imagine(pid)
    if img:
        try:
            os.remove(img)
        except OSError:
            pass


def _pozitie(v):
    """Normalizeaza o pozitie din UI; None = scoate camera de pe plan."""
    if v is None:
        return None
    try:
        x, y = float(v["x"]), float(v["y"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("poziție invalida (x, y între 0 și 1)")
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise ValueError("poziție invalida (x, y între 0 și 1)")
    try:
        d = float(v.get("dir", 0)) % 360
        fov = max(10.0, min(180.0, float(v.get("fov", 70))))
    except (TypeError, ValueError):
        raise ValueError("directie/unghi invalid")
    return {"x": round(x, 4), "y": round(y, 4), "dir": round(d, 1), "fov": round(fov, 1)}


def aseaza(pid, camere):
    """Seteaza/scoate pozitii pe un plan: {cam_id: {x,y,dir,fov} | None}. O camera sta pe
    un singur plan: asezarea pe acesta o scoate de pe celelalte."""
    with _lock:
        d = _load()
        p = next((x for x in d["planuri"] if x["id"] == pid), None)
        if not p:
            raise ValueError("planul nu există")
        p.setdefault("camere", {})
        for cid, v in (camere or {}).items():
            cid = str(int(cid))
            poz = _pozitie(v)
            if poz is None:
                p["camere"].pop(cid, None)
            else:
                p["camere"][cid] = poz
                for alt in d["planuri"]:
                    if alt is not p:
                        (alt.get("camere") or {}).pop(cid, None)
        _save(d)
    return p


def salveaza_imagine(pid, date, mime):
    """Imaginea planului, din corpul cererii (png/jpg/webp, max 8 MB)."""
    if not _ID_RE.match(pid) or not any(x["id"] == pid for x in _load()["planuri"]):
        raise ValueError("planul nu există")
    ext = TIPURI.get((mime or "").split(";")[0].strip().lower())
    if not ext:
        raise ValueError("doar PNG, JPEG sau WebP")
    if not date or len(date) > MAX_IMG:
        raise ValueError("imaginea lipsește sau depaseste 8 MB")
    # semnatura fisierului, nu doar antetul declarat
    if not (date[:8] == b"\x89PNG\r\n\x1a\n" or date[:3] == b"\xff\xd8\xff"
            or (date[:4] == b"RIFF" and date[8:12] == b"WEBP")):
        raise ValueError("continutul nu arată a imagine")
    os.makedirs(DIR, exist_ok=True)
    vechi = _imagine(pid)
    if vechi:
        os.remove(vechi)
    cale = os.path.join(DIR, f"{pid}.{ext}")
    tmp = cale + ".tmp"
    with open(tmp, "wb") as f:
        f.write(date)
    os.chmod(tmp, 0o600)
    os.replace(tmp, cale)
    return cale


def imagine(pid):
    return _imagine(pid)
