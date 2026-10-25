#!/usr/bin/env python3
"""Citire numere de inmatriculare (LPR) — ce masina a trecut prin fata camerei.

Motor: fast-alpr (detector YOLOv9 de placute + OCR CCT, ONNX local, GPU cu fallback CPU).
Acelasi tipar ca Face ID: ruleaza DOAR cand YOLO tocmai a confirmat un vehicul,
pe un cadru la rezolutie mare cerut camerei (pe substreamul de 640px placutele
sunt ilizibile de la orice distanta utila).

Fiecare citire peste prag intra intr-un jurnal (data/plates/log.jsonl) cu crop-ul
placutei — istoricul "cine a trecut" din Setari → Detectie. Placutele pot primi
nume (data/plates/known.json): la o masina cunoscuta notificarea poarta numele ei,
iar webhook-ul "numar" duce evenimentul in Node-RED (ex. deschide poarta).
"""
import json
import os
import re
import threading
import time

import numpy as np
import requests  # noqa: F401  (pastrat pentru simetrie cu faces; hd_frame vine de acolo)

import settings
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATES_DIR = os.path.join(ROOT, "data", "plates")
CROPS_DIR = os.path.join(PLATES_DIR, "crops")
LOG_FILE = os.path.join(PLATES_DIR, "log.jsonl")
KNOWN_FILE = os.path.join(PLATES_DIR, "known.json")

CROP_MARGIN = 0.25
READ_COOLDOWN = 120    # secunde: aceeasi placuta nu se jurnalizeaza de doua ori in fereastra
CONFIRM_WINDOW = 30    # secunde: o citire nesigura e acceptata doar daca acelasi numar a mai
                       # fost citit pe aceeasi camera in fereastra asta (doua citiri consecutive)
_pending = {}          # (cam, placuta) -> momentul primei citiri nesigure
LOG_MAX = 20000        # peste atat, jurnalul se taie la jumatate (cele mai vechi zboara)
CROPS_MAX = 2000       # plafonul de crop-uri pe disc

_alpr = None
_init_lock = threading.Lock()  # incarcarea modelului: un singur thread o face
_lock = threading.Lock()
_read_last = {}        # placuta -> ultimul moment jurnalizat


def enabled():
    return bool(settings.get("lpr_enabled"))


def _get_alpr():
    import ai_client
    ai_client.local_permis("LPR")
    global _alpr
    with _init_lock:
        if _alpr is None:
            import faces
            from fast_alpr import ALPR
            prov, ctx = faces.gpu_providers()
            _alpr = ALPR(detector_model="yolo-v9-t-384-license-plate-end2end",
                         ocr_model="cct-xs-v1-global-model",
                         detector_providers=list(prov), ocr_providers=list(prov))
            logare.info(f"[plates] model LPR incarcat "
                  f"({'GPU' if ctx >= 0 else 'CPU'})")
    return _alpr


def warmup_async():
    if not enabled():
        return
    threading.Thread(target=lambda: _get_alpr(), daemon=True).start()


def normalize(text):
    """'b 123-xyz' -> 'B123XYZ' (doar litere si cifre, majuscule)."""
    s = re.sub(r"[^A-Z0-9]", "", str(text or "").upper())
    if not 4 <= len(s) <= 10:
        raise ValueError("numar invalid (4-10 litere/cifre)")
    return s


# ---- placute cunoscute ----
def known():
    try:
        with open(KNOWN_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def set_known(plate, name):
    plate = normalize(plate)
    name = str(name or "").strip()[:40]
    if not name:
        raise ValueError("scrie un nume pentru mașină")
    data = known()
    data[plate] = name
    import store
    store.atomic_write_json(KNOWN_FILE, data, mode=0o600, indent=2)
    return plate


def del_known(plate):
    data = known()
    if normalize(plate) not in data:
        raise ValueError("numărul nu e în lista")
    data.pop(normalize(plate))
    import store
    store.atomic_write_json(KNOWN_FILE, data, mode=0o600, indent=2)


# ---- jurnalul de citiri ----
def _append_log(entry):
    os.makedirs(PLATES_DIR, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    try:
        if sum(1 for _ in open(LOG_FILE)) > LOG_MAX:
            import store
            store.atomic_write_text(LOG_FILE, "".join(open(LOG_FILE).readlines()[-LOG_MAX // 2:]))
    except OSError:
        pass


def reads(limit=100):
    """Cele mai noi citiri, [{t, cam, numar, incredere, nume?, crop?}]."""
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in reversed(lines[-limit * 2:]):
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out


def clear_log():
    import shutil
    for p in (LOG_FILE,):
        if os.path.isfile(p):
            os.remove(p)
    if os.path.isdir(CROPS_DIR):
        shutil.rmtree(CROPS_DIR)


def safe_path(rel):
    p = os.path.normpath(os.path.join(PLATES_DIR, rel))
    return p if p.startswith(PLATES_DIR + os.sep) else None


def _prune_crops():
    files = []
    for day in (os.listdir(CROPS_DIR) if os.path.isdir(CROPS_DIR) else []):
        dd = os.path.join(CROPS_DIR, day)
        if os.path.isdir(dd):
            files += [os.path.join(dd, f) for f in os.listdir(dd)]
    files.sort(key=os.path.getmtime)
    for p in files[:-CROPS_MAX] if len(files) > CROPS_MAX else []:
        os.remove(p)


def _save_crop(img, bbox, plate):
    import cv2
    h, w = img.shape[:2]
    x1, y1, x2, y2 = bbox
    mx, my = (x2 - x1) * CROP_MARGIN, (y2 - y1) * CROP_MARGIN
    x1, y1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
    x2, y2 = min(w, int(x2 + mx)), min(h, int(y2 + my))
    day = time.strftime("%Y-%m-%d")
    d = os.path.join(CROPS_DIR, day)
    os.makedirs(d, exist_ok=True)
    fn = f"{time.strftime('%H%M%S')}_{plate}.jpg"
    ok, buf = cv2.imencode(".jpg", img[y1:y2, x1:x2], [cv2.IMWRITE_JPEG_QUALITY, 92])
    if ok:
        with open(os.path.join(d, fn), "wb") as fh:
            fh.write(buf.tobytes())
        _prune_crops()
        return f"crops/{day}/{fn}"
    return None


# ---- fluxul per detectie de vehicul ----
def confirmata(cam_id, plate, now=None):
    """Citire cu incredere intre praguri: True doar daca acelasi numar a mai fost citit pe
    aceeasi camera in ultimele CONFIRM_WINDOW secunde (altfel o retine ca prima citire).
    Pragul coborat prinde mai multe placute reale; cerinta de doua citiri taie OCR-ul
    aiurea de pe o singura trecere."""
    now = now or time.time()
    k = (cam_id, plate)
    t0 = _pending.get(k)
    if t0 is not None and now - t0 <= CONFIRM_WINDOW:
        _pending.pop(k, None)
        return True
    _pending[k] = now
    for kk in [kk for kk, t in _pending.items() if now - t > CONFIRM_WINDOW]:
        _pending.pop(kk, None)
    return False


def stats(zile=7):
    """Cate citiri pe zi in ultimele `zile` zile (azi inclusiv), din jurnal:
    {"zile": [{"zi", "citiri", "necunoscute"}], "azi", "total7", "necunoscute7"}."""
    kn = known()
    azi = time.strftime("%Y-%m-%d")
    prag = time.strftime("%Y-%m-%d", time.localtime(time.time() - (zile - 1) * 86400))
    pe_zi = {}
    for r in reads(limit=5000):
        zi = (r.get("t") or "")[:10]
        if zi < prag:
            continue
        d = pe_zi.setdefault(zi, {"zi": zi, "citiri": 0, "necunoscute": 0})
        d["citiri"] += 1
        if not r.get("nume") and r.get("numar") not in kn:
            d["necunoscute"] += 1
    lst = sorted(pe_zi.values(), key=lambda d: d["zi"])
    return {"zile": lst, "azi": pe_zi.get(azi, {}).get("citiri", 0),
            "total7": sum(d["citiri"] for d in lst),
            "necunoscute7": sum(d["necunoscute"] for d in lst)}


def process(cam_id, sub_img, cam_entry):
    """Citeste placutele dintr-un cadru HD; intoarce numerele citite (normalizate)."""
    if not enabled():
        return []
    import faces  # refolosim cadrul HD de acolo — acelasi mecanism, aceeasi camera
    img = None
    if cam_entry and cam_entry.get("user") and cam_entry.get("pass"):
        img = faces.hd_frame(cam_entry)
    if img is None:
        img = sub_img
    if img is None:
        return []
    try:
        import metrici
        with metrici.cronometru("lpr"), _lock:
            results = _get_alpr().predict(img)
    except Exception as e:
        logare.error(f"[plates] cam{cam_id}: inferenta esuata: {str(e)[:80]}")
        return []
    min_conf = float(settings.get("lpr_min_conf"))
    sure = float(settings.get("lpr_sure_conf") or 0.9)
    kn = known()
    out = []
    now = time.time()
    for r in results:
        if not r.ocr or not r.ocr.text:
            continue
        conf = float(np.mean(r.ocr.confidence)) if r.ocr.confidence else 0.0
        try:
            plate = normalize(r.ocr.text)
        except ValueError:
            continue
        if conf < min_conf:
            continue
        if conf < sure and not confirmata(cam_id, plate, now):
            continue       # prima citire nesigura: o tinem minte si asteptam a doua
        if now - _read_last.get(plate, 0) < READ_COOLDOWN:
            continue
        _read_last[plate] = now
        bb = r.detection.bounding_box
        crop = _save_crop(img, (bb.x1, bb.y1, bb.x2, bb.y2), plate)
        nume = kn.get(plate)
        _append_log({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "cam": cam_id,
                     "numar": plate, "incredere": round(conf, 3),
                     **({"nume": nume} if nume else {}),
                     **({"crop": crop} if crop else {})})
        out.append((plate, conf, nume, (bb.x1, bb.y1, bb.x2, bb.y2)))
    if out:
        import cv2
        import snaps
        import notify
        for plate, conf, nume, (x1, y1, x2, y2) in out:
            cv2.rectangle(img, (x1, y1), (x2, y2), (60, 160, 255), 2)
            cv2.putText(img, plate + (f" ({nume})" if nume else ""),
                        (x1, max(20, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (60, 160, 255), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        jpeg = buf.tobytes() if ok else None
        for plate, conf, nume, _ in out:
            snaps.save_frame(cam_id, plate, img)
            notify.plate(cam_id, plate, nume, jpeg)
            try:
                import hooks
                hooks.emit("numar", cam_id, numar=plate, nume=nume,
                           incredere=round(conf, 3))
            except Exception:
                pass
        logare.info(f"[plates] cam{cam_id}: "
              + ", ".join(f"{p} ({c:.2f}{', ' + n if n else ''})"
                          for p, c, n, _ in out))
    return [p for p, _, _, _ in out]
