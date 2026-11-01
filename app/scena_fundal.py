#!/usr/bin/env python3
"""Fundalul invatat din verdictele omului, per camera („Invata scena”, nvr-ai).

Cand omul spune despre un candidat „nimic” / „frunze” / „umbra” / „copac”, decupajul lui
devine un exemplu de fundal al camerei: vectorul SigLIP (acelasi calculat la compararea cu
vocabularul) intra in data/scena/<cam>/fundal_invatat.{npy,json}. Un candidat nou al carui
vector e mai aproape de PRAG de un asemenea exemplu primeste automat numele acela, cu sursa
„fundal-invatat”: nu pleaca pe Telegram, nu intra in indexul cautarii, nu apare ca
„necunoscut”. Masurat pe instalatie: fundalurile aceleiasi camere seamana intre ele la
~0,88, obiectele reale (masini, gaini, oameni) raman sub 0,75 fata de orice fundal — PRAG 0,85
lasa o marja de 0,10.

Scrie nvr-ai (gazda SigLIP): la fiecare verdict de fundal (web -> /scena/fundal) si la pornire
(reconstruieste din feedback.jsonl, ca lista sa fie completa si verdictele schimbate sa iasa).
"""
import json
import os
import threading
import time

import numpy as np

import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENA_DIR = os.path.join(ROOT, "data", "scena")
PRAG = 0.85            # cosinus minim fata de un exemplu de fundal al camerei
NUME_FUNDAL = {"nimic", "frunze mișcate de vânt", "umbră", "ploaie", "copac"}
SURSA = "fundal-invatat"

_lock = threading.Lock()
_cache = {}            # cam -> (mtime json, mat, meta)


def _cai(cam):
    d = os.path.join(SCENA_DIR, str(int(cam)))
    return os.path.join(d, "fundal_invatat.npy"), os.path.join(d, "fundal_invatat.json")


def incarca(cam):
    """(matrice (N,768), meta [N]) pentru camera, din cache cat timp fisierul nu s-a schimbat."""
    npy, js = _cai(cam)
    try:
        mt = os.path.getmtime(js)
    except OSError:
        return None, []
    c = _cache.get(int(cam))
    if c and c[0] == mt:
        return c[1], c[2]
    try:
        mat, meta = np.load(npy), json.load(open(js, encoding="utf-8"))
    except (OSError, ValueError, EOFError):
        return None, []
    if mat.ndim != 2 or len(mat) != len(meta):
        return None, []
    _cache[int(cam)] = (mt, mat, meta)
    return mat, meta


def potriveste(cam, emb, prag=PRAG):
    """{nume, scor, poza} al celui mai apropiat exemplu de fundal al camerei, daca trece pragul."""
    if emb is None:
        return None
    mat, meta = incarca(cam)
    if mat is None or not len(mat):
        return None
    s = mat @ np.asarray(emb, np.float32)
    i = int(np.argmax(s))
    if float(s[i]) < prag:
        return None
    return {"nume": meta[i]["nume"], "scor": round(float(s[i]), 3), "poza": meta[i]["poza"]}


def _scrie(cam, mat, meta):
    npy, js = _cai(cam)
    os.makedirs(os.path.dirname(js), exist_ok=True)
    np.save(npy + ".tmp.npy", np.asarray(mat, np.float32).reshape(-1, 768) if len(mat) else np.zeros((0, 768), np.float32))
    os.replace(npy + ".tmp.npy", npy)
    with open(js + ".tmp", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(js + ".tmp", js)
    _cache.pop(int(cam), None)


def _vector(cam, poza):
    import semantic
    from PIL import Image
    cale = os.path.join(SCENA_DIR, str(int(cam)), poza)
    img = Image.open(cale).convert("RGB")
    return semantic.embed_images([img])[0]


def adauga(cam, poza, nume):
    """Decupajul `poza` al camerei devine exemplu de fundal cu numele dat (sau isi schimba numele).
    Intoarce True daca a scris ceva; ridica OSError daca poza nu mai exista."""
    if nume not in NUME_FUNDAL or not poza:
        return False
    with _lock:
        mat, meta = incarca(cam)
        mat = mat if mat is not None else np.zeros((0, 768), np.float32)
        for m in meta:
            if m["poza"] == poza:
                if m["nume"] == nume:
                    return False
                m["nume"] = nume
                _scrie(cam, mat, meta)
                return True
        e = _vector(cam, poza)
        _scrie(cam, np.vstack([mat, e[None, :]]), meta + [{"poza": poza, "nume": nume, "t": time.strftime("%Y-%m-%d %H:%M:%S")}])
    logare.info(f"[scena] fundal invatat cam{cam}: +{nume} ({poza}), {len(meta) + 1} exemple")
    return True


def scoate(cam, poza):
    """Verdictul s-a schimbat (nu mai e fundal): exemplul iese."""
    with _lock:
        mat, meta = incarca(cam)
        if mat is None:
            return False
        idx = [i for i, m in enumerate(meta) if m["poza"] == poza]
        if not idx:
            return False
        ramase = [i for i in range(len(meta)) if i not in idx]
        _scrie(cam, mat[ramase], [meta[i] for i in ramase])
    return True


def reconstruieste():
    """Aliniaza exemplele la feedback.jsonl (ultimul verdict al fiecarui candidat conteaza):
    verdictele de fundal fara exemplu intra, exemplele al caror verdict nu mai e fundal ies.
    Intoarce (adaugate, scoase)."""
    import scena_web
    fb = scena_web.feedback_citeste()
    pe_cam = {}
    for r in fb.values():
        if r.get("poza"):
            pe_cam.setdefault(int(r["cam"]), {})[r["poza"]] = r
    plus = minus = 0
    for cam, randuri in pe_cam.items():
        _, meta = incarca(cam)
        are = {m["poza"]: m["nume"] for m in meta}
        for poza, r in randuri.items():
            fundal = r.get("verdict") == "gresit" and r.get("eticheta") in NUME_FUNDAL
            if fundal and are.get(poza) != r["eticheta"]:
                try:
                    plus += bool(adauga(cam, poza, r["eticheta"]))
                except Exception as e:
                    logare.info(f"[scena] fundal cam{cam} {poza}: {str(e)[:60]}")
            elif not fundal and poza in are:
                minus += bool(scoate(cam, poza))
    if plus or minus:
        logare.info(f"[scena] fundal invatat: +{plus} -{minus} exemple din verdicte")
    return plus, minus


def numar(cam):
    _, meta = incarca(cam)
    return len(meta)
