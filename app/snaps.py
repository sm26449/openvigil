#!/usr/bin/env python3
"""Snapshot-uri la detectii YOLO si alarme de camera + listare pentru tabul "Alarme".

Fisiere: data/snapshots/YYYY-MM-DD/HHMMSS_cam<id>_<eticheta>.jpg
Cooldown 60s per (camera, eticheta) ca sa nu umplem discul cand gainile se agita;
stergere automata dupa KEEP_DAYS zile (prune reprogramat zilnic).
"""
import os
import re
import shutil
import threading
import time

import requests

import config as C
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SNAP_DIR = os.path.join(ROOT, "data", "snapshots")

COOLDOWN = 60

_last = {}
_lock = threading.Lock()


def _cooldown_ok(cam_id, label):
    with _lock:
        now = time.time()
        if now - _last.get((cam_id, label), 0) < COOLDOWN:
            return False
        _last[(cam_id, label)] = now
        return True


def _save_bytes(cam_id, label, data):
    day = time.strftime("%Y-%m-%d")
    d = os.path.join(SNAP_DIR, day)
    os.makedirs(d, exist_ok=True)
    fn = f"{time.strftime('%H%M%S')}_cam{cam_id}_{label}.jpg"
    with open(os.path.join(d, fn), "wb") as f:
        f.write(data)
    try:   # indexul de cautare semantica primeste fiecare poza noua (daca e pornit)
        import semantic
        semantic.enqueue(day, fn, data)
    except Exception as e:
        logare.info(f"[snaps] indexare semantica {fn}: {str(e)[:60]}")
    return os.path.join(d, fn)


def capture(cam_id, label):
    """Ia un cadru din go2rtc si il salveaza (folosit la alarmele camerei, async)."""
    if not _cooldown_ok(cam_id, label):
        return
    def work():
        try:
            r = requests.get(f"{C.GO2RTC_API}/api/frame.jpeg",
                             params={"src": f"cam{cam_id}"}, timeout=8)
            if r.ok and r.content:
                _save_bytes(cam_id, label, r.content)
        except requests.RequestException:
            pass
    threading.Thread(target=work, daemon=True).start()


def save_frame(cam_id, label, img, boxes=None):
    """Salveaza un cadru deja decodat (ndarray BGR, eventual adnotat de YOLO). `boxes`
    ([{cls, x, y, w, h, conf}] normalizate) merg intr-un sidecar <poza>.json — „gaseste-l pe
    toate camerele” taie omul exact din chenarul de la momentul detectiei, fara re-detectie."""
    if not _cooldown_ok(cam_id, label):
        return
    import cv2
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if ok:
        cale = _save_bytes(cam_id, label, buf.tobytes())
        if boxes and cale:
            try:
                store.atomic_write_json(cale + ".json", {"boxes": boxes}, mode=0o644)
            except Exception as e:
                logare.info(f"[snaps] sidecar nescris: {str(e)[:60]}")


DATASET_DIR = os.path.join(ROOT, "data", "dataset")
_last_ds = {}


def save_dataset(cam_id, img):
    """Cadru CURAT (fara chenare), materia prima a datasetului de fine-tune local
    (scripts/finetune_dataset.py). Max unul pe minut per camera; nu se sterg automat."""
    with _lock:
        now = time.time()
        if now - _last_ds.get(cam_id, 0) < 60:
            return
        _last_ds[cam_id] = now
    import cv2
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if ok:
        os.makedirs(DATASET_DIR, exist_ok=True)
        fn = f"cam{cam_id}-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
        with open(os.path.join(DATASET_DIR, fn), "wb") as f:
            f.write(buf.tobytes())


def list_day(day, limit=None):
    """Snapshot-urile unei zile, cele mai noi primele: [{time, cam, label, url}].
    Fara limit intoarce tot: gruparea in intamplari are nevoie de ziua intreaga, iar un
    plafon tacut ar face-o sa para mai linistita decat e (5812 cadre aratate ca 1000)."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
        return []
    try:
        files = sorted(os.listdir(os.path.join(SNAP_DIR, day)), reverse=True)
    except FileNotFoundError:
        return []
    out = []
    for fn in (files[:limit] if limit else files):
        m = re.match(r"^(\d{6})_cam(\d+)_(.+)\.jpg$", fn)
        if m:
            out.append({"time": f"{m[1][:2]}:{m[1][2:4]}:{m[1][4:]}",
                        "cam": int(m[2]), "label": m[3],
                        "url": f"/snapshots/{day}/{fn}"})
    return out


# Doua cadre de pe aceeasi camera la mai putin de atat distanta sunt aceeasi intamplare.
# Salvarea are deja un racord de 60s per (camera, eticheta), deci fereastra TREBUIE sa fie
# mai mare decat el, altfel doua cadre ale aceleiasi etichete n-ar lega niciodata.
# Masurat pe doua zile reale (5812 si 3028 cadre): 60s aproape nu grupeaza (1.3x), 150s da
# 5x, 300s da 8-13x, 600s da 19-31x dar incepe sa lipeasca intamplari despartite. 300s =
# "activitate intre 10:00 si 10:05" e si intuitiv, si eficient. Reglabil din interfata.
INCIDENT_GAP = 300
# O intamplare care tine ore intregi (declansari continue noaptea) e adevarata, dar devine
# un sac in care se pierde orice s-ar intampla inauntru. O taiem la jumatate de ora:
# activitatea sustinuta apare ca mai multe blocuri, fiecare inca navigabil.
INCIDENT_MAX = 1800


def incidents(day, cam=None):
    """Cadrele unei zile, grupate in intamplari.

    O persoana care traverseaza curtea produce zeci de cadre; ca lista, sunt zeci de
    randuri pe care nimeni nu le parcurge. Grupate, sunt o singura intamplare cu un
    interval, un cadru reprezentativ si etichetele gasite — adica ceva ce chiar poti citi.
    """
    import settings
    gap = settings.get("incident_gap_sec") or INCIDENT_GAP
    items = [i for i in list_day(day) if cam is None or i["cam"] == cam]
    items.sort(key=lambda i: (i["cam"], i["time"]))
    out = []
    for it in items:
        h, m, sec = (int(x) for x in it["time"].split(":"))
        t = h * 3600 + m * 60 + sec
        gr = out[-1] if out else None
        if (gr and gr["cam"] == it["cam"] and t - gr["_last"] <= gap
                and t - gr["sec"] < INCIDENT_MAX):
            gr["_last"] = t
            gr["end"] = it["time"]
        else:
            gr = {"cam": it["cam"], "start": it["time"], "end": it["time"],
                  "sec": t, "_last": t, "poze": [], "etichete": {}}
            out.append(gr)
        gr["poze"].append({"time": it["time"], "url": it["url"], "label": it["label"]})
        gr["etichete"][it["label"]] = gr["etichete"].get(it["label"], 0) + 1
    for gr in out:
        gr.pop("_last")
        gr["nr"] = len(gr["poze"])
        gr["durata"] = _secunde(gr["end"]) - gr["sec"]
        # cadrul reprezentativ: al etichetei celei mai rare din intamplare (un om intre
        # zece cadre de miscare e mai interesant decat al unsprezecelea cadru de miscare)
        rar = min(gr["etichete"], key=lambda l: gr["etichete"][l])
        gr["coperta"] = next(p["url"] for p in gr["poze"] if p["label"] == rar)
    out.sort(key=lambda g: g["sec"], reverse=True)
    return out


def _secunde(hms):
    h, m, s = (int(x) for x in hms.split(":"))
    return h * 3600 + m * 60 + s


def prune():
    """Sterge zilele mai vechi decat retentia setata (pozele ocupa foarte putin, deci
    pot fi pastrate mult mai mult decat inregistrarile). Se reprogrameaza peste 24h."""
    import settings
    keep_days = settings.get("retention_snapshots_days")
    try:
        for name in os.listdir(SNAP_DIR):
            if re.match(r"^\d{4}-\d{2}-\d{2}$", name):
                age = (time.time() - time.mktime(time.strptime(name, "%Y-%m-%d"))) / 86400
                if age > keep_days:
                    shutil.rmtree(os.path.join(SNAP_DIR, name), ignore_errors=True)
    except FileNotFoundError:
        pass
    try:   # indexul semantic urmareste snapshot-urile: zilele sterse ies si din index
        import semantic
        semantic.prune()
    except Exception as e:
        logare.error(f"[snaps] curatarea indexului semantic a esuat: {str(e)[:60]}")
    try:   # arhiva de corpuri (re-ID) are aceeasi retentie ca pozele
        import reid_arhiva
        reid_arhiva.curata()
    except Exception as e:
        logare.error(f"[snaps] curatarea arhivei re-ID a esuat: {str(e)[:60]}")
    # reprogramarea nu depinde de reusita curatarii — lectia de la record.prune
    t = threading.Timer(86400, prune)
    t.daemon = True
    t.start()
