#!/usr/bin/env python3
"""YOLO-World pentru vocabularul deschis al „Invata scena” (in nvr-ai).

SigLIP compara TOT decupajul cu numele din vocabular — pe un decupaj cu mult fundal, fundalul
castiga („umbra”, „frunze”). YOLO-World e un detector: cauta in decupaj obiectul care se
potriveste cu un nume din vocabular si da o cutie + o incredere, deci vede „om pe bicicleta”
si cand ocupa un sfert din decupaj. Modelul: yolov8m-worldv2 (ultralytics, greutati de pe
GitHub), cu numele codificate prin CLIP ViT-B/32 (din transformers, ca SigLIP/Qwen).

CLIP stie doar engleza, iar vocabularul e in romana: fiecare nume primeste o forma engleza
(scena_clasa.engleza: „nume = english name” in vocabular, altfel dictionarul en.json, altfel
traducere prin Qwen, memorata). Modelul si codificatorul se incarca lenes, intr-un fir
(warmup_async) — prima folosire descarca ~120 MB si nu are voie sa blocheze bucla camerelor.

ultralytics isi codifica el numele cu pachetul `clip` (instalat din git la prima folosire);
imaginea e inchisa (lock cu hash-uri, fara pip la rulare), asa ca punem noi `txt_feats`
(aceleasi greutati OpenAI ViT-B/32, prin transformers).
"""
import os
import threading

import logare
import settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "modele")
MODEL_FN = "yolov8m-worldv2.pt"
CLIP_ID = "openai/clip-vit-base-patch32"
CONF = 0.15             # pragul detectorului; decizia finala e in scena_clasa (PRAG_LUME)

_model = None
_clip = None
_clase = None           # tuple cu numele (romana) pentru care sunt setate txt_feats
_nume = {}              # indice de clasa -> nume (romana), pentru rezultate
_lock = threading.Lock()
_init_lock = threading.Lock()
_stare = {"gata": False, "eroare": None, "porneste": False}


def activ():
    return bool(settings.get("scena_lume"))


def stare():
    return {**_stare, "model": MODEL_FN, "clase": len(_clase or ())}


def _device():
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def _get_clip():
    global _clip
    with _init_lock:
        if _clip is None:
            import torch
            from transformers import CLIPTextModelWithProjection, CLIPTokenizer
            tok = CLIPTokenizer.from_pretrained(CLIP_ID)
            m = CLIPTextModelWithProjection.from_pretrained(CLIP_ID, dtype=torch.float32).float()
            m = m.to(_device()).eval()
            _clip = (tok, m)
    return _clip


def _text_feats(nume_en):
    """(1, n, 512) — ce ar fi dat clip.encode_text(clip.tokenize(nume)), normalizat."""
    import torch
    tok, m = _get_clip()
    with torch.no_grad():
        ins = tok(list(nume_en), padding=True, truncation=True, max_length=77, return_tensors="pt")
        ins = {k: v.to(m.device) for k, v in ins.items()}
        e = m(**ins).text_embeds.float()
        e = e / e.norm(dim=-1, keepdim=True)
    return e.reshape(1, len(nume_en), -1)


def _get_model():
    global _model
    with _init_lock:
        if _model is None:
            from ultralytics import YOLO
            from ultralytics.utils.downloads import attempt_download_asset
            os.makedirs(DIR, exist_ok=True)
            cale = os.path.join(DIR, MODEL_FN)
            if not os.path.isfile(cale):
                attempt_download_asset(cale)
            m = YOLO(cale)
            m.to(_device())
            _model = m
            logare.info(f"[scena-lume] {MODEL_FN} incarcat ({_device()})")
    return _model


def _seteaza_clase(nume_ro):
    """txt_feats pentru vocabularul curent (o data per schimbare a listei)."""
    global _clase, _nume
    import scena_clasa
    chei = tuple(nume_ro)
    if _clase == chei:
        return
    m = _get_model()
    en = [scena_clasa.engleza(n) for n in nume_ro]
    feats = _text_feats(en).to(next(m.model.parameters()).device)
    m.model.txt_feats = feats
    m.model.model[-1].nc = len(nume_ro)
    nume = {i: n for i, n in enumerate(nume_ro)}
    m.model.names = nume
    if m.predictor:
        m.predictor.model.names = nume
    _nume = nume
    _clase = chei
    logare.info(f"[scena-lume] vocabular: {len(nume_ro)} nume ({', '.join(en[:4])}…)")


def pregateste():
    """Incarca modelul + codificatorul si seteaza vocabularul; True cand e gata."""
    import scena_clasa
    try:
        with _lock:
            _seteaza_clase(scena_clasa.vocabular())
        _stare.update(gata=True, eroare=None)
    except Exception as e:
        _stare.update(gata=False, eroare=str(e)[:120])
        logare.info(f"[scena-lume] nu pornesc: {str(e)[:120]}")
    _stare["porneste"] = False
    return _stare["gata"]


def warmup_async():
    """La pornirea nvr-ai (daca sunt camere cu scena): descarcare + incarcare in fundal."""
    if not activ() or _stare["gata"] or _stare["porneste"]:
        return
    _stare["porneste"] = True
    threading.Thread(target=pregateste, daemon=True, name="scena-lume-warmup").start()


def detecteaza(crop_bgr, imgsz=320, quantize=None):
    """Cel mai sigur obiect din decupaj, dupa vocabular: {eticheta, incredere} sau None.
    None si cand modelul nu e gata (nu asteapta: bucla camerelor nu se opreste)."""
    if not activ() or crop_bgr is None or crop_bgr.size == 0:
        return None
    if not _stare["gata"]:
        warmup_async()
        return None
    import scena_clasa
    with _lock:
        try:
            _seteaza_clase(scena_clasa.vocabular())      # vocabularul s-a putut schimba
            m = _get_model()
            import metrici
            with metrici.cronometru("yolo_world"):
                try:
                    res = m.predict(crop_bgr, imgsz=imgsz, conf=CONF, verbose=False, quantize=quantize)[0]
                except TypeError:
                    res = m.predict(crop_bgr, imgsz=imgsz, conf=CONF, verbose=False)[0]
        except Exception as e:
            logare.info(f"[scena-lume] predict: {str(e)[:80]}")
            return None
    best = None
    for b in res.boxes:
        conf = float(b.conf)
        if best is None or conf > best["incredere"]:
            best = {"eticheta": _nume.get(int(b.cls), "?"), "incredere": round(conf, 3)}
    return best



def detecteaza_nume(crop_bgr, nume_en, imgsz=640, conf=0.1):
    """Chenarul celui mai sigur obiect numit `nume_en` (engleza, o singura clasa) din decupaj:
    {xyxy, incredere} sau None. Pentru construirea datasetului din feedback (chenarul
    obiectului pe care omul l-a numit). Lasa vocabularul invalidat: urmatorul `detecteaza`
    il reseteaza."""
    global _clase
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    with _lock:
        m = _get_model()
        feats = _text_feats([nume_en]).to(next(m.model.parameters()).device)
        m.model.txt_feats = feats
        m.model.model[-1].nc = 1
        m.model.names = {0: nume_en}
        if m.predictor:
            m.predictor.model.names = {0: nume_en}
        _clase = None
        res = m.predict(crop_bgr, imgsz=imgsz, conf=conf, verbose=False)[0]
    best = None
    for b in res.boxes:
        c = float(b.conf)
        if best is None or c > best["incredere"]:
            best = {"xyxy": [float(v) for v in b.xyxy[0].tolist()], "incredere": round(c, 3)}
    return best
