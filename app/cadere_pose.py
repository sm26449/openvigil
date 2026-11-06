#!/usr/bin/env python3
"""Pose (keypoints) pentru regula de cădere — rulează în nvr-ai (are GPU + modelul).
Model mic (yolo11n-pose), încărcat lenef, pe decupajul persoanei. Întoarce keypoints COCO-17
în coordonatele decupajului; `reguli.scor_cadere` folosește doar geometrie relativă, deci e ok.

Separat de `cadere.py` (orchestratorul) ca acela să rămână testabil fără model. Aliniat la
tiparul din `scena_lume` (data/modele, attempt_download_asset, _device).
"""
import os
import threading

import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "modele")
MODEL_FN = "yolo11n-pose.pt"

_model = None
_init_lock = threading.Lock()
_lock = threading.Lock()         # serializează inferențele (o instanță în VRAM)


def _device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _get_model():
    global _model
    with _init_lock:
        if _model is None:
            import ai_client
            ai_client.local_permis("Cadere")        # nu încărca modelul în web când rulează pe nvr-ai
            from ultralytics import YOLO
            from ultralytics.utils.downloads import attempt_download_asset
            os.makedirs(DIR, exist_ok=True)
            cale = os.path.join(DIR, MODEL_FN)
            if not os.path.isfile(cale):
                attempt_download_asset(cale)
            m = YOLO(cale)
            m.to(_device())
            _model = m
            logare.info(f"[cadere] {MODEL_FN} incarcat ({_device()})")
    return _model


def warmup_async():
    threading.Thread(target=lambda: _get_model(), daemon=True).start()


def keypoints(img, bbox, imgsz=320):
    """Keypoints COCO-17 [(x, y, incredere)] ai celei mai proeminente persoane din decupajul
    bbox, sau None dacă nu s-a găsit niciunul. Coordonate relative la decupaj."""
    import numpy as np
    h, w = img.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in bbox)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    crop = img[y1:y2, x1:x2]
    try:
        with _lock:
            res = _get_model().predict(crop, imgsz=imgsz, verbose=False)[0]
    except Exception as e:
        logare.info(f"[cadere] pose eșuat: {str(e)[:80]}")
        return None
    kp = getattr(res, "keypoints", None)
    if kp is None or kp.data is None or len(kp.data) == 0:
        return None
    # cea mai „sigură" persoană (suma increderii keypoints-urilor)
    data = kp.data.cpu().numpy() if hasattr(kp.data, "cpu") else np.asarray(kp.data)
    best = max(data, key=lambda p: float(p[:, 2].sum()))
    return [(float(x), float(y), float(c)) for x, y, c in best]
