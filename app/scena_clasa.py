#!/usr/bin/env python3
"""Clasificarea unui decupaj „ceva nou” (din scena.py): ce e obiectul din regiunea schimbata.

Doua pareri, pe decupaj (obiectul umple imaginea, deci si un om de 20 px din cadru devine
clasificabil):
  1. YOLO (modelul activ al instalatiei) pe decupaj, la 320 px — eticheta si increderea;
  2. vocabularul deschis: SigLIP (modelul cautarii semantice, deja incarcat) compara decupajul
     cu lista de nume a instalatiei („om”, „om pe bicicletă”, „cal cu căruță”, „tractor”…),
     editabila din Setari — un nume nou nu cere niciun antrenament.
Decizia: YOLO cand e sigur (>= PRAG_YOLO); altfel numele din vocabular daca iese clar in fata
celorlalte; altfel „necunoscut” — si exact necunoscutele sunt cele pe care le aratam omului
in fisa camerei, ca sa ne spuna ce sunt (feedback-ul intra la antrenament).
Textele vocabularului sunt in romana (SigLIP e multilingv); interfata le traduce la afisare.
"""
import threading

import numpy as np

import logare
import settings

PRAG_YOLO = 0.5          # increderea YOLO de la care nu mai intrebam vocabularul
PRAG_VOCAB = 0.05        # scorul SigLIP (cosinus) sub care nu credem niciun nume
MARJA_VOCAB = 0.01       # cat trebuie sa fie peste al doilea nume ca sa fie „clar”

VOCABULAR_IMPLICIT = [
    "om", "om pe bicicletă", "om cu cărucior", "copil", "mașină", "camion", "tractor",
    "autobuz", "motocicletă", "cal", "cal cu căruță", "câine", "pisică", "găină", "porumbel",
    "pasăre", "vulpe", "mistreț", "oaie", "vacă", "frunze mișcate de vânt", "umbră", "ploaie",
]

_lock = threading.Lock()
_vocab_cache = {"chei": None, "emb": None}


def vocabular():
    v = settings.get("scena_vocabular")
    v = [str(x).strip() for x in (v or []) if str(x).strip()]
    return v or list(VOCABULAR_IMPLICIT)


def _embeddings_vocab(nume):
    """Embedding-urile SigLIP ale numelor, memorate pana se schimba lista."""
    import semantic
    chei = tuple(nume)
    with _lock:
        if _vocab_cache["chei"] != chei:
            _vocab_cache["emb"] = np.stack([semantic.embed_text(n) for n in nume])
            _vocab_cache["chei"] = chei
        return _vocab_cache["emb"]


def _yolo(crop_bgr, model, clase, quantize=None):
    try:
        res = model.predict(crop_bgr, imgsz=320, conf=0.25, verbose=False, quantize=quantize)[0]
    except TypeError:                      # ultralytics fara `quantize`
        res = model.predict(crop_bgr, imgsz=320, conf=0.25, verbose=False)[0]
    best = None
    for b in res.boxes:
        lbl = clase.get(int(b.cls)) if clase else None
        if lbl is None:
            continue
        conf = float(b.conf)
        if best is None or conf > best["incredere"]:
            best = {"eticheta": lbl, "incredere": round(conf, 3)}
    return best


def _vocab(crop_bgr):
    import cv2
    import semantic
    from PIL import Image
    if not semantic.enabled():
        return None
    nume = vocabular()
    emb = _embeddings_vocab(nume)
    pil = Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB))
    e = semantic.embed_images([pil])[0]
    scoruri = emb @ e
    ordine = np.argsort(-scoruri)[:3]
    return [{"nume": nume[i], "scor": round(float(scoruri[i]), 4)} for i in ordine]


def clasifica(crop_bgr, model=None, clase=None, quantize=None):
    """{eticheta, incredere, sursa, yolo, vocab} pentru un decupaj BGR."""
    rez = {"eticheta": "necunoscut", "incredere": 0.0, "sursa": "-", "yolo": None, "vocab": None}
    if crop_bgr is None or crop_bgr.size == 0:
        return rez
    if model is not None:
        try:
            rez["yolo"] = _yolo(crop_bgr, model, clase or {}, quantize)
        except Exception as e:
            logare.info(f"[scena] yolo pe decupaj: {str(e)[:80]}")
    try:
        rez["vocab"] = _vocab(crop_bgr)
    except Exception as e:
        logare.info(f"[scena] vocabular: {str(e)[:80]}")
    y, v = rez["yolo"], rez["vocab"]
    if y and y["incredere"] >= PRAG_YOLO:
        rez.update(eticheta=y["eticheta"], incredere=y["incredere"], sursa="yolo")
    elif v and v[0]["scor"] >= PRAG_VOCAB and (len(v) < 2 or v[0]["scor"] - v[1]["scor"] >= MARJA_VOCAB):
        rez.update(eticheta=v[0]["nume"], incredere=v[0]["scor"], sursa="vocabular")
    elif y:
        rez.update(eticheta=y["eticheta"], incredere=y["incredere"], sursa="yolo-nesigur")
    return rez
