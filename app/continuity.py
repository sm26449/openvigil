#!/usr/bin/env python3
"""Garantia ca inregistram — si ca ce am inregistrat chiar sta pe disc cat spune retentia.

Un sistem de supraveghere care nu inregistreaza arata identic cu unul care inregistreaza,
pana in ziua in care ai nevoie de filmare. De aceea nu ne bazam pe "procesul traieste" sau
"camera raspunde": singura dovada e ce se vede pe disc.

Trei intrebari, la care raspundem separat:

1. SCRIE ACUM?  Varsta ultimei scrieri. Peste prag = lantul e mort, chiar daca ffmpeg
   pare viu si camera raspunde la ping.
2. AM PIERDUT CEVA?  Acoperirea zilnica, calculata din fisiere si tinuta intr-un jurnal —
   altfel, dupa ce retentia sterge fisierele, nu mai avem cum sti daca ziua aceea a fost
   intreaga sau gaurita.
3. INCAPE RETENTIA?  Cat scriem pe zi fata de cat spatiu avem. Daca garda de spatiu ar
   trebui sa stearga inregistrari mai tinere decat retentia configurata, promisiunea din
   interfata e falsa si trebuie spus, nu ascuns.
"""
import json
import os
import shutil
import threading
import time

import record
import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(ROOT, "data", "continuity.json")
EXT_FILE = os.path.join(ROOT, "data", "cameras.json")

MUT_SEC = record.STALL_SEC * 2   # peste atat fara scriere = raportam, nu doar repornim
KEEP_DAYS = 90                   # jurnalul de acoperire e minuscul; il tinem mult
MIN_GAP = record.SEGMENT * 2.5   # sub atat nu e gol, e doar granita dintre segmente

_lock = threading.RLock()

# Calculele grele (parcurgerea a sute de GB de fisiere, listarea a mii de segmente) se
# refolosesc cateva zeci de secunde. Fara asta, /api/continuity dura 2.5 secunde si bloca
# deschiderea paginii de setari la fiecare afisare, desi raspunsul se schimba lent.
_memo = {}


def _cache(cheie, sec, fn):
    acum = time.time()
    v = _memo.get(cheie)
    if v and acum - v[0] < sec:
        return v[1]
    rez = fn()
    _memo[cheie] = (acum, rez)
    return rez


def _cams():
    try:
        with open(EXT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def _load():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save(data):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, STATE_FILE)


def _day_bounds(day):
    start = time.mktime(time.strptime(day, "%Y-%m-%d"))
    return start, start + 86400


def intervals(cam_id, day):
    """Intervalele acoperite de inregistrari intr-o zi, unite: [(start, end)] epoch."""
    a, b = _day_bounds(day)
    now = time.time()
    runs = []
    for t, _, _ in record._files(cam_id):
        s, e = t, min(t + record.SEGMENT, now)
        if e <= a or s >= b:
            continue
        s, e = max(s, a), min(e, b)
        if runs and s - runs[-1][1] < 15:   # lipite: tolereaza repornirile scurte
            runs[-1][1] = max(runs[-1][1], e)
        else:
            runs.append([s, e])
    return [(s, e) for s, e in runs]


def gaps(cam_id, day):
    return _cache(("gaps", cam_id, day), 45, lambda: _gaps(cam_id, day))


def _gaps(cam_id, day):
    """Golurile reale dintr-o zi: [{"start","end","sec"}]. Doar pana in momentul curent, si
    doar de la prima inregistrare incoace (inainte de asta nu exista gol, exista neinceput)."""
    a, b = _day_bounds(day)
    b = min(b, time.time())
    files = record._files(cam_id)
    if files:
        a = max(a, min(a + 86400, files[0][0]))
    runs = intervals(cam_id, day)
    if not runs:
        return [{"start": a, "end": b, "sec": b - a}] if b > a else []
    out = []
    if runs[0][0] - a > MIN_GAP:
        out.append((a, runs[0][0]))
    for (_, e1), (s2, _) in zip(runs, runs[1:]):
        if s2 - e1 > MIN_GAP:
            out.append((e1, s2))
    if b - runs[-1][1] > MIN_GAP:
        out.append((runs[-1][1], b))
    return [{"start": s, "end": e, "sec": round(e - s)} for s, e in out]


def day_stats(cam_id, day, mod="continuous"):
    """Acoperirea unei zile pentru o camera. La modul "la miscare" acoperirea partiala e
    normala (se sterg segmentele fara miscare), deci nu inseamna pierdere.

    Fereastra asteptata incepe cel devreme la prima inregistrare a camerei: nu putem fi
    vinovati pentru orele dinainte sa pornim, iar altfel prima zi ar aparea mereu rosie."""
    a, b = _day_bounds(day)
    b = min(b, time.time())
    files = record._files(cam_id)
    if files:
        a = max(a, min(a + 86400, files[0][0]))
    asteptat = max(0, b - a)
    acoperit = sum(e - s for s, e in intervals(cam_id, day))
    g = gaps(cam_id, day)
    return {"acoperit": round(acoperit), "asteptat": round(asteptat), "mod": mod,
            "procent": round(acoperit / asteptat * 100, 1) if asteptat else None,
            "goluri": len(g) if mod == "continuous" else 0,
            "gol_max": max((x["sec"] for x in g), default=0) if mod == "continuous" else 0}


def refresh(days=2):
    """Actualizeaza jurnalul pentru ultimele zile. Zilele incheiate raman inghetate acolo
    chiar dupa ce retentia sterge fisierele — asa ramanem cu dovada ca ziua a fost intreaga."""
    with _lock:
        data = _load()
        for cam in _cams():
            if cam.get("record", "continuous") == "off":
                continue
            cid = str(cam["id"])
            for k in range(days):
                day = time.strftime("%Y-%m-%d", time.localtime(time.time() - k * 86400))
                st = day_stats(cam["id"], day, cam.get("record", "continuous"))
                if st["asteptat"]:
                    data.setdefault(cid, {})[day] = st
        limit = time.strftime("%Y-%m-%d", time.localtime(time.time() - KEEP_DAYS * 86400))
        for cid in list(data):
            data[cid] = {d: v for d, v in data[cid].items() if d >= limit}
        _save(data)
        return data


def journal(cam_id=None, days=14):
    """Jurnalul de acoperire, pentru interfata."""
    limit = time.strftime("%Y-%m-%d", time.localtime(time.time() - days * 86400))
    data = _load()
    if cam_id is not None:
        d = data.get(str(cam_id), {})
        return [dict(zi=k, **v) for k, v in sorted(d.items()) if k >= limit]
    return {cid: [dict(zi=k, **v) for k, v in sorted(d.items()) if k >= limit]
            for cid, d in data.items()}


def live():
    """Starea de acum: scrie fiecare camera? De cat timp nu s-a mai scris nimic?"""
    out = []
    now = time.time()
    for cam in _cams():
        mod = cam.get("record", "continuous")
        if mod == "off":
            continue
        lw = record.last_write(cam["id"])
        varsta = None if lw is None else round(now - lw)
        rec = record.recorders.get(cam["id"])
        out.append({"id": cam["id"], "name": cam["name"], "mod": mod,
                    "ultima_scriere": lw, "varsta": varsta,
                    "scrie": varsta is not None and varsta < MUT_SEC,
                    "reporniri": getattr(rec, "stalls", 0) if rec else 0})
    return out


_frac_cache = {"t": 0, "v": {}}


def event_fraction():
    """Ce parte din segmente contin evenimente — de ea depinde cat spatiu cere retentia.

    Retentia are doua trepte: segmentele goale traiesc `retention_days`, cele cu
    evenimente `retention_event_days`. Daca aproape toate segmentele contin un eveniment,
    treapta scurta nu se aplica aproape niciodata si tot discul se umple la ritmul treptei
    lungi. Fara cifra asta, orice estimare de spatiu e mult prea optimista."""
    import bisect
    if time.time() - _frac_cache["t"] < 600:
        return _frac_cache["v"]
    out = {}
    for cam in _cams():
        cid = cam["id"]
        files = record._files(cid)
        if not files:
            continue
        ev = sorted(record.event_times(cid, days=3))
        tot = cu = 0
        for t, _, _ in files[-500:]:            # ultimele ~40h sunt destul de reprezentative
            tot += 1
            a, b = t - record.MOTION_PAD, t + record.SEGMENT + record.MOTION_PAD
            i = bisect.bisect_left(ev, a)
            if i < len(ev) and ev[i] <= b:
                cu += 1
        out[cid] = cu / tot if tot else 0.0
    _frac_cache.update(t=time.time(), v=out)
    return out


def _gb(path):
    tot = 0
    for r, _, fs in os.walk(path):
        for f in fs:
            try:
                tot += os.path.getsize(os.path.join(r, f))
            except OSError:
                pass
    return tot / 2**30


def forecast():
    return _cache("forecast", 60, _forecast)


def _forecast():
    """Incape retentia configurata in spatiul disponibil?

    Masuram cat ocupa efectiv fiecare camera si impartim la zilele acoperite — asa iese
    ritmul real, nu unul teoretic din bitrate. Apoi comparam ce ar trebui sa incapa
    (fiecare camera cu retentia ei) cu ce avem. Daca nu incape, garda de spatiu va sterge
    inregistrari mai tinere decat retentia promisa, si atunci setarea din interfata minte."""
    import settings
    min_free = settings.get("retention_min_free_gb")
    data = _load()
    frac = event_fraction()
    per_cam, nevoie = [], 0.0
    for cam in _cams():
        if cam.get("record", "continuous") == "off":
            continue
        cid = cam["id"]
        d = os.path.join(record.REC_DIR, f"cam{cid}")
        if not os.path.isdir(d):
            continue
        gb = _gb(d)
        zile = data.get(str(cid), {})
        files = record._files(cid)
        span = (time.time() - files[0][0]) / 86400 if files else 0
        # Cat costa pe disc o ZI DE FUNCTIONARE: marimea de pe disc impartita la timpul
        # scurs de la prima inregistrare. Nu la numarul de zile din jurnal (o zi inceputa
        # la pranz ar conta ca zi intreaga si ritmul ar iesi de doua ori mai mic), si nici
        # la timpul acoperit (la camerele pe "la miscare" se pastreaza doar o parte din zi,
        # deci ar iesi mult mai mare decat costa in realitate).
        zile_ef = max(span, 0.05)
        gb_zi = gb / zile_ef
        ret_zile, ret_ev = record._cam_retention(cid)
        fr = frac.get(cid, 0.0)
        # segmentele cu evenimente traiesc mai mult; amestecul da necesarul real
        cere = gb_zi * (fr * ret_ev + (1 - fr) * ret_zile)
        nevoie += cere
        per_cam.append({"id": cid, "name": cam["name"], "gb": round(gb, 1),
                        "gb_pe_zi": round(gb_zi, 2), "zile_pastrate": ret_zile,
                        "zile_evenimente": ret_ev, "cu_evenimente": round(fr * 100),
                        "necesar_gb": round(cere, 1), "zile_pe_disc": round(span, 1)})
    try:
        cale = record.REC_DIR          # la prima pornire nu exista inca: urcam la parinte
        while not os.path.isdir(cale) and os.path.dirname(cale) != cale:
            cale = os.path.dirname(cale)
        u = shutil.disk_usage(cale)
        liber, total = u.free / 2**30, u.total / 2**30
    except OSError:
        liber = total = 0
    folosit = sum(c["gb"] for c in per_cam)
    disponibil = folosit + liber - min_free      # cat putem ocupa fara sa lovim garda
    gb_zi_tot = sum(c["gb_pe_zi"] for c in per_cam)
    return {"camere": sorted(per_cam, key=lambda c: -c["necesar_gb"]),
            "folosit_gb": round(folosit, 1), "liber_gb": round(liber, 1),
            "total_gb": round(total, 1), "rezerva_gb": min_free,
            "necesar_gb": round(nevoie, 1), "disponibil_gb": round(disponibil, 1),
            "gb_pe_zi": round(gb_zi_tot, 1),
            "zile_posibile": round(disponibil / gb_zi_tot, 1) if gb_zi_tot > 0.01 else None,
            "cu_evenimente": round(sum(frac.values()) / len(frac) * 100) if frac else 0,
            "incape": nevoie <= disponibil,
            "propunere": _propunere(disponibil, gb_zi_tot, frac)}


def _propunere(disponibil, gb_zi, frac):
    """Ce retentie chiar incape, cu o marja de siguranta.

    Cand aproape toate segmentele contin evenimente, treapta de retentie lunga se aplica
    la aproape tot, deci nu are rost sa promitem doua numere diferite: propunem aceeasi
    valoare pentru amandoua si spunem de ce. Daca fractia scade (mai putine fals-pozitive),
    treapta lunga redevine utila si propunerea creste singura."""
    if gb_zi < 0.01:
        return None
    zile = int(disponibil / gb_zi * 0.85)          # 15% marja pentru varfuri de trafic
    med = (sum(frac.values()) / len(frac)) if frac else 0
    if med > 0.5:
        return {"zile": max(1, zile), "zile_evenimente": max(1, zile),
                "motiv": t("{procent}% din segmente contin evenimente, deci treapta lunga s-ar aplica oricum la aproape tot. Doua numere diferite ar fi o promisiune fara acoperire.",
                           procent=round(med * 100))}
    lung = int(disponibil / (gb_zi * (med + (1 - med) * 0.35)) * 0.85)
    return {"zile": max(1, zile), "zile_evenimente": max(1, min(lung, zile * 4)),
            "motiv": t("Segmentele fara evenimente pot fi sterse mai devreme, deci treapta lunga incape.")}


def problems():
    """Ce nu e in regula chiar acum. Fiecare are o cheie stabila, ca notificarile sa se
    poata suprima intre ele si sa nu ne trezim cu acelasi mesaj din minut in minut."""
    out = []
    for c in live():
        if not c["scrie"]:
            v = t("niciodata") if c["varsta"] is None else t("de {n} min", n=c['varsta'] // 60)
            out.append({"cheie": f"mut-{c['id']}", "cam_id": c["id"], "grav": True,
                        "text": t("{cam}: nu s-a mai scris nimic pe disc {de}", cam=c['name'], de=v)})
    azi = time.strftime("%Y-%m-%d")
    for cam in _cams():
        if cam.get("record", "continuous") != "continuous":
            continue
        for g in gaps(cam["id"], azi):
            if g["sec"] < 600:          # sub 10 minute: raportat doar in interfata
                continue
            ora = time.strftime("%H:%M", time.localtime(g["start"]))
            out.append({"cheie": f"gol-{cam['id']}-{int(g['start'] // 600)}",
                        "cam_id": cam["id"], "grav": True,
                        "text": t("{cam}: gol de {n} min in inregistrare, incepand {ora}",
                                  cam=cam['name'], n=g['sec'] // 60, ora=ora)})
    f = forecast()
    if not f["incape"]:
        out.append({"cheie": "retentie", "grav": True,
                    "text": t("Retentia configurata cere {necesar} GB, dar avem loc de {disponibil} GB. Inregistrarile vor fi sterse mai devreme decat scrie in setari.",
                              necesar=f['necesar_gb'], disponibil=f['disponibil_gb'])})
    return out


def start(on_problem=None, interval=120):
    """Bucla de supraveghere: reimprospateaza jurnalul si raporteaza problemele."""
    def work():
        while True:
            try:
                refresh()
                if on_problem:
                    for p in problems():
                        on_problem(p)
            except Exception as e:
                logare.info(f"[continuitate] {e}")
            time.sleep(interval)
    t = threading.Thread(target=work, daemon=True, name="continuitate")
    t.start()
    return t
