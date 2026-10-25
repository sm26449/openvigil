#!/usr/bin/env python3
"""Coada cadrelor „nesigure”: cadrele din data/dataset pe care modelul ACTIV le vede cu
incredere intre LO si HI (nici sigur, nici nimic). Astea sunt cadrele din care un om scoate cel
mai mult cu o corectie: pre-etichetate cu chenarele modelului (cu increderea lor), le confirmi
sau le corectezi in editor, intra in data/finetune/images/train ca verificate.

Scanarea ruleaza in nvr-ai (modelul e incarcat acolo), intr-un fir, peste cele mai noi N cadre
care nu sunt deja in dataset sau in coada. Rezultatul: data/finetune/nesigure.json
  {fn: {"boxes": [{cls, cx, cy, w, h, conf}], "scor": nr_chenare_nesigure, "t": "..."}}
"""
import os
import threading
import time

import store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASET_DIR = os.path.join(ROOT, "data", "dataset")
FINETUNE = os.path.join(ROOT, "data", "finetune")
FISIER = os.path.join(FINETUNE, "nesigure.json")
LO, HI = 0.30, 0.55
_lock = threading.Lock()
stare = {"activ": False, "progres": 0, "total": 0, "gasite": 0, "t": None, "eroare": None}


def incarca():
    return store.load_json(FISIER, {}) or {}


def _scrie(d):
    os.makedirs(FINETUNE, exist_ok=True)
    store.atomic_write_json(FISIER, d, mode=0o644)


class _lacat:
    """Lacat INTRE procese (fcntl) pe coada: nvr-ai o completeaza in timp ce nvr-web scoate
    din ea; fara el, un read-modify-write dintr-un proces anula scrierea celuilalt."""
    def __enter__(self):
        import fcntl
        os.makedirs(FINETUNE, exist_ok=True)
        self.f = open(FISIER + ".lock", "w")
        fcntl.flock(self.f, fcntl.LOCK_EX)
        _lock.acquire()
        return self
    def __exit__(self, *a):
        import fcntl
        _lock.release()
        fcntl.flock(self.f, fcntl.LOCK_UN)
        self.f.close()


def scoate(fn):
    """Scoate un cadru din coada (dupa salvare in dataset sau „ignora”)."""
    with _lacat():
        d = incarca()
        if fn in d:
            d.pop(fn)
            _scrie(d)
            return True
    return False


def _in_dataset():
    out = set()
    for sp in ("train", "val"):
        d = os.path.join(FINETUNE, "images", sp)
        out |= set(os.listdir(d)) if os.path.isdir(d) else set()
    return out


def candidati(n):
    """Cele mai noi n cadre din data/dataset care nu sunt in dataset si nici in coada."""
    existente = _in_dataset() | set(incarca())
    # numele e cam<id>-YYYYMMDD-HHMMSS.jpg: „cele mai noi” = dupa data, nu dupa id-ul camerei
    files = sorted((f for f in os.listdir(DATASET_DIR) if f.endswith(".jpg") and f not in existente),
                   key=lambda f: f.split("-", 1)[1] if "-" in f else f, reverse=True) if os.path.isdir(DATASET_DIR) else []
    return files[:n]


def _boxes(res, nume_clase):
    """Chenarele unui rezultat ultralytics -> [{cls (id in dataset), cx, cy, w, h, conf}]."""
    out = []
    idx = {n: i for i, n in enumerate(nume_clase)}
    for b in res.boxes:
        nume = res.names[int(b.cls)]
        if nume not in idx:
            continue
        cx, cy, w, h = b.xywhn[0].tolist()
        out.append({"cls": idx[nume], "cx": round(cx, 5), "cy": round(cy, 5),
                    "w": round(w, 5), "h": round(h, 5), "conf": round(float(b.conf), 3)})
    return out


def scaneaza(n=500):
    """Porneste scanarea in fundal; False daca ruleaza deja."""
    with _lock:
        if stare["activ"]:
            return False
        stare.update(activ=True, progres=0, total=0, gasite=0, t=time.time(), eroare=None)
    threading.Thread(target=_scan, args=(int(n),), daemon=True, name="nesigure").start()
    return True


def _scan(n):
    import logare
    try:
        import cv2
        import adnotare
        import detect
        nume_clase = adnotare.clase()
        files = candidati(n)
        stare["total"] = len(files)
        gasite = {}
        for i, fn in enumerate(files):
            img = cv2.imread(os.path.join(DATASET_DIR, fn))
            if img is None:
                continue
            with detect._lock:
                model = detect._get_model()
                res = model.predict(img, imgsz=640, conf=0.25, device=detect._device, verbose=False)[0]
            boxes = _boxes(res, nume_clase)
            nes = [b for b in boxes if LO <= b["conf"] < HI]
            if nes:
                gasite[fn] = {"boxes": boxes, "scor": len(nes), "t": time.strftime("%Y-%m-%d %H:%M:%S")}
            stare["progres"] = i + 1
            stare["gasite"] = len(gasite)
            if gasite and (i + 1) % 50 == 0:
                with _lacat():
                    d = incarca(); d.update(gasite); _scrie(d)
                gasite = {}           # ce e scris nu se mai rescrie (altfel ar reveni ce a scos web-ul)
        with _lacat():
            d = incarca(); d.update(gasite); _scrie(d)
        logare.info(f"[nesigure] {len(files)} cadre scanate, {len(gasite)} nesigure")
    except Exception as e:
        stare["eroare"] = str(e)[:160]
        logare.error(f"[nesigure] scanarea a esuat: {str(e)[:160]}")
    finally:
        stare["activ"] = False
