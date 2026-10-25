#!/usr/bin/env python3
"""A doua opinie cu YOLO stock (yolo11m, 80 clase COCO) pentru detectiile 'person'
incerte ale modelului fine-tuned curte-v1.

De ce: curte-v1 e bun ziua, dar la lumina slaba/amurg biaseaza spre 'person' pe forme
ambigue — caini, vehicule la distanta, tufe. Modelul stock (neantrenat pe curtea asta,
dar robust pe COCO) e o a doua parere: daca pe acelasi crop stock vede clar un vehicul
sau un animal, atunci 'person' de la curte-v1 e gresit si il corectam la eticheta stock.

Ruleaza DOAR pe cutiile 'person' cu incredere sub un prag (cele foarte sigure le lasam),
deci e ieftin — nu pe fiecare cadru, ci pe rarele detectii de persoana incerte."""
import os
import threading

import numpy as np
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STOCK_PATH = os.path.join(ROOT, "data", "yolo11m_stock.pt")

# ce "dezminte" un person: daca stock vede una din astea dominant pe crop, nu e om
NON_PERSON = {"car", "truck", "bus", "motorcycle", "bicycle", "dog", "cat",
              "horse", "bird", "cow", "sheep", "bear"}
TRUST_ABOVE = 0.85     # person peste atat il credem fara verificare (evident)
STOCK_MIN = 0.45       # cat de sigur trebuie sa fie stock ca sa dezmintă
MARGIN = 0.15          # context in jurul cutiei la decupare

_model = None
_device = None
_lock = threading.Lock()


def _get_model():
    global _model, _device
    with _lock:
        if _model is None:
            import torch
            from ultralytics import YOLO
            m = YOLO(STOCK_PATH)
            # Incercam GPU, dar daca VRAM-ul e plin (nvr-web tine deja curte-v1 + Qwen +
            # SigLIP) cadem pe CPU: verificarea e rara (doar pe 'person' incert) si asincrona,
            # deci ~1-2s pe CPU e acceptabil, iar un OOM ar fi mult mai rau.
            _device = "cuda" if torch.cuda.is_available() else "cpu"
            if _device == "cuda":
                try:
                    m.predict(np.zeros((320, 320, 3), "uint8"), imgsz=320,
                              device="cuda", verbose=False)
                except torch.cuda.OutOfMemoryError:
                    torch.cuda.empty_cache()
                    _device = "cpu"
                    logare.info("[verify] VRAM plin -> stock pe CPU")
            if _device == "cpu":
                torch.set_num_threads(8)
            _model = m
            logare.info(f"[verify] model stock yolo11m incarcat ({_device})")
    return _model


def _crop(img, box_rel):
    """box_rel = (x, y, w, h) normalizate [0,1] -> decupaj BGR cu marja de context."""
    h, w = img.shape[:2]
    x, y, bw, bh = box_rel
    mx, my = bw * MARGIN, bh * MARGIN
    x1 = max(0, int((x - mx) * w)); y1 = max(0, int((y - my) * h))
    x2 = min(w, int((x + bw + mx) * w)); y2 = min(h, int((y + bh + my) * h))
    if x2 <= x1 or y2 <= y1:
        return None
    return img[y1:y2, x1:x2]


def second_label(crop_bgr):
    """Eticheta dominanta pe care o vede stock pe un crop (sau None). Cu increderea ei."""
    if crop_bgr is None or crop_bgr.size == 0:
        return None, 0.0
    m = _get_model()
    with _lock:
        res = m.predict(crop_bgr, imgsz=320, conf=STOCK_MIN, device=_device,
                        verbose=False)[0]
    best, bestc = None, 0.0
    for b in res.boxes:
        c = float(b.conf)
        if c > bestc:
            best, bestc = m.names[int(b.cls)], c
    return best, round(bestc, 2)


def refine(img, boxes):
    """Corecteaza etichetele 'person' incerte dintr-o lista de cutii (dict cu
    lbl/conf/x/y/w/h normalizate). Muteaza si intoarce lista. Nu atinge non-person
    sau person foarte sigur. Daca stock vede clar un non-person pe crop -> reeticheteaza."""
    for b in boxes:
        if b.get("lbl") != "person" or b.get("conf", 1.0) >= TRUST_ABOVE:
            continue
        crop = _crop(img, (b["x"], b["y"], b["w"], b["h"]))
        lbl, c = second_label(crop)
        if lbl in NON_PERSON and c >= STOCK_MIN:
            b["lbl"] = lbl
            b["conf"] = c
            b["corectat"] = True     # marcam ca a fost dezmintit de a doua opinie
    return boxes
