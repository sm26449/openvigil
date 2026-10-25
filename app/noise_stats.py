#!/usr/bin/env python3
"""Statistici orare de zgomot, pastrate pe disc, ca sa avem cu ce calibra pragurile.

O valoare pe secunda inseamna 3600 pe ora si peste 600.000 pe saptamana per camera —
prea multe ca sa le tinem. Le comprimam intr-un histograma cu bin de 1 dB, din care
scoatem exact percentilele; la inchiderea orei pastram doar cifrele, nu si esantioanele.
Cost pe disc: cateva sute de octeti pe camera pe zi.

Doua alegeri care conteaza:

1. MEDIA SE FACE PE ENERGIE, nu pe decibeli. dB e logaritmic, deci media aritmetica a
   valorilor in dB nu are sens fizic si ascunde tocmai varfurile care ne intereseaza:
   o secunda la -10 dB intr-o ora de -60 dB abia misca media aritmetica, dar ridica
   vizibil energia totala. Marimea corecta e Leq (nivel echivalent continuu), la fel
   ca in acustica: Leq = 10*log10( media(rms^2) ).

2. PERCENTILELE SPUN MAI MULT DECAT MIN/MAX. Un singur cadru corupt strica maximul pe
   toata ora. In acustica se folosesc L90 (nivelul depasit 90% din timp = fondul real
   al locului) si L10 (nivelul depasit 10% din timp = zgomotul care se remarca). Le
   pastram pe amandoua, plus mediana si un p99 pentru evenimentele rare.
"""
import atexit
import json
import math
import os
import threading
import time
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATS_FILE = os.path.join(ROOT, "data", "noise_stats.json")

KEEP_DAYS = 30          # istoricul e minuscul, il putem tine mult peste cele 7 zile cerute
DB_MIN, DB_MAX = -90, 0  # binuri de 1 dB; sub -90 dBFS e digital tacere
FLUSH_SEC = 60          # cat de des salvam ora in curs (ca un restart sa nu o piarda)

_lock = threading.RLock()
_hours = {}     # (cam_id, "YYYY-MM-DDTHH") -> {"n","pow","min","max","hist":[...]}
_last_flush = 0


def _key(t):
    return time.strftime("%Y-%m-%dT%H", time.localtime(t))


def _blank():
    return {"n": 0, "pow": 0.0, "min": None, "max": None,
            "hist": [0] * (DB_MAX - DB_MIN + 1)}


def _resume(cam_id, hk):
    """Ora in curs, recuperata de pe disc dupa un restart de serviciu.

    Fara asta, un restart la 09:50 ar face ca urmatorul salvat sa suprascrie ora 09
    cu cele cateva minute ramase — deci fiecare repornire ar falsifica o ora intreaga.
    De aceea pastram histograma pe disc DOAR pentru ora in curs; la inchiderea ei
    raman doar cifrele."""
    old = _load().get(str(cam_id), {}).get(hk)
    if not old or "hist" not in old:
        return _blank()
    return {"n": old["n"], "pow": 10 ** (old["leq"] / 10.0) * old["n"],
            "min": old["min"], "max": old["max"], "hist": old["hist"]}


def add(cam_id, db, now=None):
    """Un esantion (o secunda de audio). Se apeleaza din AudioMonitor."""
    now = now or time.time()
    with _lock:
        k = (cam_id, _key(now))
        if k not in _hours:
            _hours[k] = _resume(cam_id, k[1])
        rec = _hours[k]
        rec["n"] += 1
        rec["pow"] += 10 ** (db / 10.0)       # inapoi in energie, ca sa putem media corect
        rec["min"] = db if rec["min"] is None else min(rec["min"], db)
        rec["max"] = db if rec["max"] is None else max(rec["max"], db)
        idx = min(DB_MAX - DB_MIN, max(0, int(round(db)) - DB_MIN))
        rec["hist"][idx] += 1
        _maybe_flush(now)


def _pct(hist, n, p):
    """Percentila din histograma: valoarea sub care sta p% din timp."""
    target = n * p / 100.0
    run = 0
    for i, c in enumerate(hist):
        run += c
        if run >= target:
            return i + DB_MIN
    return DB_MAX


def _summary(rec):
    n = rec["n"]
    if not n:
        return None
    return {"n": n,
            "leq": round(10 * math.log10(rec["pow"] / n), 1),   # media energetica
            "min": rec["min"], "max": rec["max"],
            "l90": _pct(rec["hist"], n, 10),    # depasit 90% din timp = fondul real
            "med": _pct(rec["hist"], n, 50),
            "l10": _pct(rec["hist"], n, 90),    # depasit 10% din timp = ce se remarca
            "p99": _pct(rec["hist"], n, 99)}


def _load():
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _maybe_flush(now):
    global _last_flush
    if now - _last_flush < FLUSH_SEC:
        return
    _last_flush = now
    try:
        save()
    except OSError as e:
        logare.error(f"[noise] nu pot salva statisticile: {e}")


def save():
    """Scrie orele acumulate peste fisierul de pe disc si taie ce e prea vechi."""
    with _lock:
        data = _load()
        cur = _key(time.time())
        for (cam_id, hk), rec in _hours.items():
            s = _summary(rec)
            if s:
                if hk == cur:
                    s["hist"] = rec["hist"]   # doar ora in curs, ca s-o putem relua
                data.setdefault(str(cam_id), {})[hk] = s
        for cam in data.values():             # curata histogramele orelor deja incheiate
            for hk, v in cam.items():
                if hk != cur:
                    v.pop("hist", None)
        limit = time.strftime("%Y-%m-%dT%H",
                              time.localtime(time.time() - KEEP_DAYS * 86400))
        for cam in list(data):
            data[cam] = {k: v for k, v in data[cam].items() if k >= limit}
            if not data[cam]:
                del data[cam]
        tmp = STATS_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, STATS_FILE)
        # orele incheiate nu mai primesc esantioane: le scoatem din memorie
        for k in [k for k in _hours if k[1] != cur]:
            del _hours[k]


def _on_exit():
    if not _hours:      # nimic acumulat (teste, scripturi care doar importa modulul):
        return          # nu rescriem fisierul — ar ramane pe utilizatorul procesului
    try:
        save()
    except OSError:
        pass


atexit.register(_on_exit)


def hourly(cam_id, days=7):
    """Orele din ultimele N zile, cronologic: [{"h":"2026-08-04T00", ...}]."""
    limit = time.strftime("%Y-%m-%dT%H", time.localtime(time.time() - days * 86400))
    data = _load().get(str(cam_id), {})
    with _lock:   # ora in curs nu e inca pe disc
        for (cid, hk), rec in _hours.items():
            if cid == cam_id:
                s = _summary(rec)
                if s:
                    data[hk] = s
    return [dict(h=k, **{x: y for x, y in v.items() if x != "hist"})
            for k, v in sorted(data.items()) if k >= limit]


def profile(cam_id, days=7):
    """Profilul pe ora din zi: cele 24 de ore, mediate peste toate zilele colectate.

    Asta raspunde la "cum suna locul la ora 3 noaptea, in general" — exact ce trebuie
    ca sa alegem un prag care nu suna degeaba. Leq se mediaza tot pe energie."""
    buckets = {}
    for row in hourly(cam_id, days):
        b = buckets.setdefault(int(row["h"][-2:]),
                               {"pow": 0.0, "n": 0, "min": [], "max": [], "l90": [], "l10": []})
        b["pow"] += 10 ** (row["leq"] / 10.0) * row["n"]
        b["n"] += row["n"]
        b["min"].append(row["min"])
        b["max"].append(row["max"])
        b["l90"].append(row["l90"])
        b["l10"].append(row["l10"])
    out = []
    for h in range(24):
        b = buckets.get(h)
        if not b or not b["n"]:
            out.append({"ora": h, "n": 0})
            continue
        out.append({"ora": h, "n": b["n"],
                    "leq": round(10 * math.log10(b["pow"] / b["n"]), 1),
                    "min": min(b["min"]), "max": max(b["max"]),
                    "l90": round(sum(b["l90"]) / len(b["l90"])),
                    "l10": round(sum(b["l10"]) / len(b["l10"])),
                    "zile": len(b["min"])})
    return out


def suggest(cam_id, days=7):
    """Praguri propuse pe baza a ce s-a masurat efectiv, nu pe ghicite.

    Prag absolut: peste cel mai zgomotos p99 orar + o marja, deci sub el ramane tot
    ce s-a intamplat normal in perioada masurata. Varf: cat de mult sare in mod
    obisnuit varful peste fond, plus marja — daca locul e linistit iese un numar mic,
    daca e langa drum iese unul mare, si asta e tocmai ideea."""
    rows = hourly(cam_id, days)
    if not rows:
        return None
    ore = len(rows)
    p99 = max(r["p99"] for r in rows)
    fond = sorted(r["l90"] for r in rows)[len(rows) // 2]          # fondul tipic
    salt = sorted(r["p99"] - r["l90"] for r in rows)[int(len(rows) * 0.9)]
    zi = [r for r in rows if 7 <= int(r["h"][-2:]) < 22]
    noapte = [r for r in rows if not (7 <= int(r["h"][-2:]) < 22)]
    dif = None
    if zi and noapte:
        dif = round(sum(r["leq"] for r in zi) / len(zi)
                    - sum(r["leq"] for r in noapte) / len(noapte), 1)
    return {"ore": ore, "zile": round(ore / 24.0, 1),
            "gata": ore >= 24 * days * 0.8,     # sub 80% acoperire, propunerea e slaba
            "fond": fond, "varf_masurat": p99,
            "prag_db": min(-3, p99 + 4),
            "spike_db": max(8, min(30, salt + 4)),
            "dif_zi_noapte": dif}
