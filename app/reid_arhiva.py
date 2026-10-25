#!/usr/bin/env python3
"""Arhiva de corpuri (re-ID) pe zile: „gaseste-l pe toate camerele”.

Galeria din reid.py traieste o zi, in memorie, si e doar pentru nume. Aici pastram pe disc
FIECARE esantion de corp calculat de reid._job (la ~3 s pe fiecare om urmarit, pe camerele
urmarite): embedding-ul OSNet (512 x float16) + un crop mic, in data/reid/<zi>.jsonl si
data/reid/<zi>/<HHMMSS>_cam<id>_<tid>.jpg. Cautarea: un embedding (din poza unui eveniment,
dintr-un crop al arhivei) -> aparitiile pe toate camerele, pe zile, cu similaritatea.
Un om cu aceleasi haine se regaseste; a doua zi, cu alte haine, nu — e limita metodei.
Retentia: cat pozele (retention_snapshots_days).
"""
import base64
import json
import os
import re
import threading
import time

import numpy as np

import logare
import settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "reid")
CROP_H = 128           # crop-ul pastrat: 128 px inaltime (~4 KB)
PRAG = 0.50            # similaritate minima ca o aparitie sa conteze
GRUP_SEC = 60          # pe aceeasi camera, intr-un minut, pastram doar cea mai buna aparitie
_lock = threading.Lock()
_cache = {}            # zi -> (mtime, E (N,512) float32, meta [dict])
_FN = re.compile(r"^\d{6}_cam\d+_\d+\.jpg$")
_ZI = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _zi_din(t):
    return time.strftime("%Y-%m-%d", time.localtime(t))


def adauga(cam_id, tid, emb, crop, cine=None, t=None):
    """Un esantion nou: embedding + crop mic. Apelat din reid._job (nvr-ai)."""
    emb = np.asarray(emb, np.float32).ravel()
    if emb.size != 512:
        logare.warning(f"[reid-arhiva] embedding neasteptat ({emb.size} valori, {type(emb).__name__}) — sarit")
        return
    t = t or time.time()
    zi = _zi_din(t)
    d = os.path.join(DIR, zi)
    os.makedirs(d, exist_ok=True)
    fn = f"{time.strftime('%H%M%S', time.localtime(t))}_cam{cam_id}_{tid}.jpg"
    try:
        import cv2
        h, w = crop.shape[:2]
        if h > CROP_H:
            crop = cv2.resize(crop, (max(1, int(w * CROP_H / h)), CROP_H), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            with open(os.path.join(d, fn), "wb") as f:
                f.write(buf.tobytes())
    except Exception as e:
        logare.info(f"[reid-arhiva] crop nescris: {str(e)[:60]}")
    rec = {"t": round(t, 1), "cam": int(cam_id), "tid": int(tid), "fn": fn,
           "emb": base64.b64encode(np.asarray(emb, np.float16).tobytes()).decode()}
    if cine:
        rec["cine"] = cine
    with _lock:
        p = os.path.join(DIR, zi + ".jsonl")
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        c = _cache.get(zi)
        if c is not None:      # ziua e in cache: adaugam randul, nu reparsam 20k linii la urmatoarea cautare
            meta = c[2] + [{k: v for k, v in rec.items() if k != "emb"}]
            E = np.vstack([c[1], np.asarray(emb, np.float32)[None, :]])
            try:
                _cache[zi] = (os.path.getmtime(p), E, meta)
            except OSError:
                _cache.pop(zi, None)


def zile():
    """Zilele cu arhiva, cele mai noi primele."""
    if not os.path.isdir(DIR):
        return []
    return sorted((f[:-6] for f in os.listdir(DIR) if f.endswith(".jsonl")), reverse=True)


def _incarca(zi):
    p = os.path.join(DIR, zi + ".jsonl")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return np.zeros((0, 512), np.float32), []
    with _lock:
        c = _cache.get(zi)
        if c and c[0] == mt:
            return c[1], c[2]
    embs, meta = [], []
    with open(p, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                e = np.frombuffer(base64.b64decode(r.pop("emb")), np.float16).astype(np.float32)
                if e.shape[0] != 512:
                    continue
                embs.append(e)
                meta.append(r)
            except (ValueError, KeyError):
                continue
    E = np.stack(embs) if embs else np.zeros((0, 512), np.float32)
    with _lock:
        if len(_cache) > 40:
            _cache.pop(next(iter(_cache)))
        _cache[zi] = (mt, E, meta)
    return E, meta


def cauta(emb, zile_max=7, limit=60, prag=PRAG, vizibile=None):
    """Aparitiile corpului `emb` pe toate camerele, in ultimele `zile_max` zile:
    [{t, zi, ora, cam, tid, sim, fn, cine}], cele mai asemanatoare primele; pe aceeasi camera,
    intr-un minut, o singura aparitie (cea mai buna)."""
    emb = np.asarray(emb, np.float32)
    emb = emb / max(float(np.linalg.norm(emb)), 1e-9)
    hits = []
    for zi in zile()[:zile_max]:
        E, meta = _incarca(zi)
        if not len(E):
            continue
        sims = E @ emb
        best = {}
        for i in np.nonzero(sims >= prag)[0]:
            m = meta[i]
            if vizibile is not None and m["cam"] not in vizibile:
                continue
            k = (m["cam"], int(m["t"] // GRUP_SEC))
            if k not in best or sims[i] > best[k][0]:
                best[k] = (float(sims[i]), m, zi)
        for s, m, z in best.values():
            hits.append({**m, "zi": z, "ora": time.strftime("%H:%M:%S", time.localtime(m["t"])),
                         "sim": round(s, 3)})
    hits.sort(key=lambda h: -h["sim"])
    return hits[:limit]


def embedding_din(zi, fn):
    """Embedding-ul unui esantion din arhiva (pentru „cauta dupa acesta”)."""
    E, meta = _incarca(zi)
    for i, m in enumerate(meta):
        if m["fn"] == fn:
            return E[i]
    return None


def cale_crop(zi, fn):
    if not _ZI.match(zi or "") or not _FN.match(fn or ""):
        return None
    p = os.path.join(DIR, zi, fn)
    return p if os.path.isfile(p) else None


def curata(zile_pastrate=None):
    """Sterge zilele mai vechi decat retentia pozelor."""
    import shutil
    n = int(zile_pastrate or settings.get("retention_snapshots_days") or 90)
    prag = time.strftime("%Y-%m-%d", time.localtime(time.time() - n * 86400))
    sterse = 0
    for zi in zile():
        if zi < prag:
            try:
                os.remove(os.path.join(DIR, zi + ".jsonl"))
                shutil.rmtree(os.path.join(DIR, zi), ignore_errors=True)
                sterse += 1
            except OSError:
                pass
    return sterse


def stare():
    zl = zile()
    n = 0
    for zi in zl[:7]:
        n += len(_incarca(zi)[1])
    return {"zile": len(zl), "esantioane_7_zile": n, "prima_zi": zl[-1] if zl else None,
            "ultima_zi": zl[0] if zl else None}
