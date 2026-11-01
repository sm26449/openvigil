#!/usr/bin/env python3
"""Exemplele invatate din verdictele omului, per camera („Invata scena”, nvr-ai).

Fiecare verdict cu un nume devine un exemplu al camerei: decupajul candidatului, cu vectorul
SigLIP (acelasi calculat la compararea cu vocabularul), intra in
data/scena/<cam>/fundal_invatat.{npy,json}. Doua feluri de nume:

- fundal (NUME_FUNDAL: „nimic”, „frunze”, „umbra”, „ploaie”, „copac”) — un candidat nou care
  seamana la peste PRAG_FUNDAL cu un asemenea exemplu primeste numele, cu sursa „fundal-invatat”:
  nu pleaca pe Telegram, nu intra in index, nu apare ca „necunoscut”. Bate orice eticheta.
- obiecte („gaina”, „porumbel”, „masina”…), din ✓ pe eticheta propusa sau ✕ cu numele adevarat —
  un candidat nou care seamana la peste PRAG_OBIECT primeste numele, cu sursa „invatat”, dar
  numai daca sistemul nu avea deja un nume de la un detector (YOLO, YOLO-World, VLM):
  inlocuieste „necunoscut” si scorurile marunte ale vocabularului.

Cel mai apropiat exemplu care isi trece pragul castiga, deci un fundal ∅ pe prelata alba si o
gaina ✓ pe aceeasi camera se despart intre ele. Masurat pe instalatie (cam 101, 1 nov 2026):
fundalurile aceleiasi camere seamana intre ele la ~0,88 si raman sub 0,75 fata de obiecte;
gainile fara nume seamana cu cele numite la 0,87 (mediana), iar prelata si pamantul ating cel
mult 0,82 fata de gaini — de aici cele doua praguri.

Scrie nvr-ai (gazda SigLIP): la fiecare verdict (web -> /scena/fundal) si la pornire
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
PRAG_FUNDAL = 0.85     # cosinus minim fata de un exemplu de fundal al camerei
PRAG_OBIECT = 0.82     # ...fata de un exemplu de obiect
PRAG = PRAG_FUNDAL
NUME_FUNDAL = {"nimic", "frunze mișcate de vânt", "umbră", "ploaie", "copac"}
SURSA = "fundal-invatat"
SURSA_OBIECT = "invatat"
SURSE_DETECTOR = {"yolo", "yolo-world", "vlm"}   # etichete pe care un exemplu de obiect nu le bate

_lock = threading.Lock()
_cache = {}            # cam -> (mtime json, mat, meta)


def e_fundal(nume):
    return nume in NUME_FUNDAL


def nume_exemplu(r):
    """Numele cu care un rand de feedback devine exemplu, sau None (verdictul „?”, „necunoscut”,
    sau un ✓ pe un candidat care nu avea nume)."""
    if r.get("verdict") == "gresit":
        nume = r.get("eticheta")
    elif r.get("verdict") == "ok":
        nume = r.get("eticheta") or r.get("eticheta_initiala")
    else:
        nume = None
    nume = (nume or "").strip()
    return nume if nume and nume != "necunoscut" else None


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


def potriveste(cam, emb):
    """{nume, scor, poza, fundal} al celui mai apropiat exemplu al camerei care isi trece pragul
    (PRAG_FUNDAL pentru fundal, PRAG_OBIECT pentru obiecte), altfel None."""
    if emb is None:
        return None
    mat, meta = incarca(cam)
    if mat is None or not len(mat):
        return None
    s = mat @ np.asarray(emb, np.float32)
    praguri = np.fromiter((PRAG_FUNDAL if e_fundal(m["nume"]) else PRAG_OBIECT for m in meta), np.float32, len(meta))
    trec = s >= praguri
    if not trec.any():
        return None
    i = int(np.argmax(np.where(trec, s, -1.0)))
    return {"nume": meta[i]["nume"], "scor": round(float(s[i]), 3), "poza": meta[i]["poza"], "fundal": e_fundal(meta[i]["nume"])}


def aplica(cam, emb, rez):
    """Rezultatul clasificarii primeste numele exemplului potrivit, daca exista si daca are voie
    (fundalul bate tot; un obiect nu bate eticheta unui detector). Intoarce potrivirea sau None."""
    f = potriveste(cam, emb)
    if not f:
        return None
    if not f["fundal"] and rez.get("sursa") in SURSE_DETECTOR:
        return None
    rez.update(eticheta=f["nume"], incredere=f["scor"], sursa=SURSA if f["fundal"] else SURSA_OBIECT)
    return f


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
    """Decupajul `poza` al camerei devine exemplu cu numele dat (sau isi schimba numele).
    Intoarce True daca a scris ceva; ridica OSError daca poza nu mai exista."""
    nume = (nume or "").strip()
    if not nume or nume == "necunoscut" or not poza:
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
    logare.info(f"[scena] exemplu invatat cam{cam}: +{nume} ({poza}), {len(meta) + 1} exemple")
    return True


def scoate(cam, poza):
    """Verdictul s-a schimbat (nu mai are nume): exemplul iese."""
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
    verdictele cu nume fara exemplu intra, exemplele al caror verdict nu mai are nume ies.
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
            nume = nume_exemplu(r)
            if nume and are.get(poza) != nume:
                try:
                    plus += bool(adauga(cam, poza, nume))
                except Exception as e:
                    logare.info(f"[scena] exemplu cam{cam} {poza}: {str(e)[:60]}")
            elif not nume and poza in are:
                minus += bool(scoate(cam, poza))
    if plus or minus:
        logare.info(f"[scena] exemple invatate: +{plus} -{minus} din verdicte")
    return plus, minus


def numar(cam):
    """(exemple de fundal, exemple de obiecte) ale camerei."""
    _, meta = incarca(cam)
    f = sum(1 for m in meta if e_fundal(m["nume"]))
    return f, len(meta) - f
