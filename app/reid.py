#!/usr/bin/env python3
"""Re-ID pe corp (OSNet): "cine e omul asta?" fara sa-i vedem fata.

Camerele sunt montate sus — fata se vede rar (practic doar la usa, cam101).
OSNet scoate un embedding de infatisare (silueta + imbracaminte) din crop-ul
intregului corp, la orice unghi. Regula de aur: FATA ancoreaza identitatea,
CORPUL o propaga:

  1. cand Face ID recunoaste pe cineva, corpul care CONTINE fata (YOLO pe
     acelasi cadru HD) intra in galeria zilei (ancoreaza) — nu depinde de
     camerele urmarite, usa nu e pe track_cams;
  2. bucla de urmarire esantioneaza la ~PAS secunde crop-uri de corp pentru
     oamenii urmariti (sample) — dintr-un cadru HD tras la cerere (pietonii
     de pe strada au ~60-80px pe substream, prea putin; in HD au 3-4x), cu
     substreamul ca fallback;
  3. un track fara nume se compara cu galeria: peste PRAG primeste numele —
     "stefan a mers de la poarta pe alee" fara sa se uite la camera.

Galeria traieste O ZI (in memorie): infatisarea = haine, iar hainele se
schimba de la o zi la alta. A doua zi, prima recunoastere de fata o
reancoreaza de la zero. Modelul e ONNX pe CPU (~1MB, cateva ms per lot) —
nu concureaza cu GPU-ul pentru VRAM. Fetch-ul HD + inferenta ruleaza intr-un
executor propriu (un job per camera): bucla batch nu asteapta niciodata.
"""
import os
import threading
import time

import numpy as np

import settings
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(ROOT, "data", "osnet_x0_25_msmt17.onnx")

PRAG = 0.6          # similaritate cosinus minima ca un track sa primeasca nume
PAS = 3.0           # secunde intre doua esantioane pe acelasi track
MIN_H_PX = 96       # inaltimea minima a crop-ului pe cadrul HD (si la ancorare)
MIN_H_PX_SUB = 72   # fallback substream: prag mai mic, pixelii sunt mai rari
MIN_W_PX = 24
MARGINE = 0.08      # box-ul e de pe substream, cadrul HD e tras ACUM — absorbim drift
MAX_PER_TRACK = 5   # embeddings pastrate per track (cele mai noi)
MAX_GALERIE = 40    # embeddings per persoana per zi (ancorele vechi se rotesc)
BATCH = 16          # dimensiunea FIXA de lot a modelului ONNX (se umple cu zero)

_sess = None
_pool = None
_lock = threading.Lock()
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)

_galerie = {"zi": "", "persoane": {}}    # nume -> [embedding L2-normalizat]


def activ():
    return bool(settings.get("reid_enabled")) and os.path.isfile(MODEL_PATH)


def _get_sess():
    global _sess
    with _lock:
        if _sess is None:
            import onnxruntime as ort
            _sess = ort.InferenceSession(MODEL_PATH,
                                         providers=["CPUExecutionProvider"])
            logare.info("[reid] model OSNet incarcat (CPU)")
    return _sess


def embed(crops):
    """Crop-uri BGR de corp intreg -> embeddings L2-normalizate (N, 512)."""
    import cv2
    import metrici
    sess = _get_sess()
    out = []
    with metrici.cronometru("reid"):
        for i in range(0, len(crops), BATCH):
            lot = np.zeros((BATCH, 3, 256, 128), np.float32)
            parte = crops[i:i + BATCH]
            for j, c in enumerate(parte):
                img = cv2.cvtColor(cv2.resize(c, (128, 256)), cv2.COLOR_BGR2RGB)
                lot[j] = ((img / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)
            out.append(sess.run(None, {"input": lot})[0][:len(parte)])
    emb = np.concatenate(out)
    return emb / np.maximum(np.linalg.norm(emb, axis=1, keepdims=True), 1e-9)


def _azi():
    return time.strftime("%Y-%m-%d")


def _persoane():
    """Galeria zilei curente; la schimbarea zilei se goleste (haine noi)."""
    if _galerie["zi"] != _azi():
        _galerie.update(zi=_azi(), persoane={})
    return _galerie["persoane"]


def ancora(nume, embs):
    """Embeddings-uri de corp CONFIRMATE (fata vazuta) intra in galeria zilei."""
    lst = _persoane().setdefault(nume, [])
    lst.extend(np.asarray(e) for e in embs)
    del lst[:-MAX_GALERIE]


def cine(emb):
    """Cel mai apropiat om din galeria zilei: (nume, sim) peste PRAG, altfel
    (None, cea mai buna similaritate) — utila la diagnosticare."""
    best, best_sim = None, 0.0
    for nume, lst in _persoane().items():
        s = float(max(e @ emb for e in lst)) if lst else 0.0
        if s > best_sim:
            best, best_sim = nume, s
    return (best, best_sim) if best_sim >= PRAG else (None, best_sim)


# ---- ancorarea (Face ID -> galerie) ----

def ancoreaza(cam_id, nume, img=None, bbox=None):
    """Face ID a recunoscut `nume`. Doua ancore, independente:
    - din CADRU (img + bbox-ul fetei): corpul care contine fata -> galerie.
      Precisa (fata alege corpul, nu ghicim) si merge pe ORICE camera;
    - din TRACK: daca pe camera e urmarit exact un om, trackul primeste numele
      (episodul curent afla cine e) si embeddings-urile lui intra in galerie.
      Cu doi oameni urmariti nu ghicim — o ancora gresita otraveste ziua."""
    if not activ():
        return False
    ok = False
    if img is not None and bbox is not None:
        ok = _ancora_din_cadru(nume, img, bbox)
    t = _tracker(cam_id)
    if t is not None:
        oameni = [tr for tr in t.tracks.values() if tr["lbl"] == "person"]
        if len(oameni) == 1:
            tr = oameni[0]
            tr["cine"], tr["cine_sursa"] = nume, "fata"
            if tr.get("reid"):
                ancora(nume, tr["reid"])
            ok = True
    return ok


def _ancora_din_cadru(nume, img, face_bbox):
    """Corpul CARUIA ii apartine fata: box-ul YOLO de persoana care contine
    centrul fetei; la suprapuneri castiga cel cu fata cel mai aproape de crestet
    (fata sta la marginea de sus a corpului)."""
    import detect
    try:
        boxes = detect.persoane(img)
    except Exception as e:
        logare.info(f"[reid] ancora din cadru: {str(e)[:70]}")
        return False
    fx = (face_bbox[0] + face_bbox[2]) / 2
    fy = (face_bbox[1] + face_bbox[3]) / 2
    candidati = [b for b in boxes if b[0] <= fx <= b[2] and b[1] <= fy <= b[3]]
    if not candidati:
        return False
    x1, y1, x2, y2 = (int(v) for v in min(candidati, key=lambda b: abs(fy - b[1])))
    if y2 - y1 < MIN_H_PX or x2 - x1 < MIN_W_PX:
        return False
    crop = img[max(y1, 0):y2, max(x1, 0):x2]
    if crop.size == 0:
        return False
    try:
        ancora(nume, [embed([crop])[0]])
    except Exception as e:
        logare.info(f"[reid] embed la ancorare: {str(e)[:70]}")
        return False
    logare.info(f"[reid] ancora din cadru: {nume} ({y2 - y1}px)")
    return True


# ---- esantionarea din bucla de urmarire ----

def sample(tracker, frame, now):
    """Alege trackurile scadente si trimite jobul (HD + embed) in executor —
    bucla care ne cheama nu asteapta nici fetch-ul HD, nici inferenta."""
    if frame is None or not activ() or getattr(tracker, "_reid_busy", False):
        return
    import track
    due = []
    for tid, t in tracker.tracks.items():
        if (t["lbl"] != "person" or t["n"] < track.MIN_FRAMES
                or now - t.get("reid_t", 0.0) < PAS or "nbox" not in t):
            continue
        t["reid_t"] = now
        due.append((tid, t["nbox"]))
    if not due:
        return
    tracker._reid_busy = True
    _submit(_job, tracker, frame.copy(), due)


def _submit(fn, *args):
    """Seam pentru teste (inlocuit cu apel sincron); in productie, executorul."""
    global _pool
    with _lock:
        if _pool is None:
            from concurrent.futures import ThreadPoolExecutor
            _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reid")
    _pool.submit(fn, *args)


def _job(tracker, sub_frame, due):
    """Jobul asincron al unei camere: cadru HD (fallback substream) -> crop-uri
    -> embeddings -> nume din galerie / hranirea galeriei."""
    try:
        hd = _hd_frame(tracker.cam_id)
        src, prag = (hd, MIN_H_PX) if hd is not None else (sub_frame, MIN_H_PX_SUB)
        h, w = src.shape[:2]
        lot = []
        for tid, (nx, ny, nw, nh) in due:
            mx, my = nw * MARGINE, nh * MARGINE
            px, py = max(int((nx - mx) * w), 0), max(int((ny - my) * h), 0)
            px2 = min(int((nx + nw + mx) * w), w)
            py2 = min(int((ny + nh + my) * h), h)
            if py2 - py < prag or px2 - px < MIN_W_PX:
                continue
            crop = src[py:py2, px:px2]
            if crop.size:
                lot.append((tid, crop))
        if not lot:
            return
        embs = embed([c for _, c in lot])
        for (tid, crop), e in zip(lot, embs):
            t = tracker.tracks.get(tid)
            if t is None:
                continue                     # trackul a expirat intre timp
            try:
                import reid_arhiva
                reid_arhiva.adauga(tracker.cam_id, tid, e, crop, cine=t.get("cine"))
            except Exception as ex:
                logare.info(f"[reid] arhiva: {str(ex)[:60]}")
            t.setdefault("reid", []).append(e)
            del t["reid"][:-MAX_PER_TRACK]
            if t.get("cine_sursa") == "fata":
                ancora(t["cine"], [e])       # fata l-a confirmat: intareste galeria
            elif "cine" not in t:
                nume, sim = cine(e)
                if nume:
                    t["cine"], t["cine_sursa"] = nume, "reid"
                    t["cine_sim"] = round(sim, 2)
                    logare.info(f"[reid] cam{tracker.cam_id}: {nume} "
                          f"(sim {sim:.2f}, corp)")
    except Exception as e:
        logare.error(f"[reid] cam{tracker.cam_id}: job esuat: {str(e)[:70]}")
    finally:
        tracker._reid_busy = False


def _hd_frame(cam_id):
    """Cadrul mainstream (ISAPI) al camerei, daca avem acces; altfel None.
    Substreamul (4:3) e mainstream-ul STRANS, nu taiat — box-urile normalizate
    se mapeaza direct (verificat vizual pe cam13, 2026-09-27)."""
    try:
        import detect
        import faces
        entry = detect._ext_cam(cam_id)
        if entry and entry.get("user") and entry.get("pass"):
            return faces.hd_frame(entry)
    except Exception:
        pass
    return None


def _tracker(cam_id):
    """Trackerul viu al camerei, oriunde ruleaza el (bucla nvr-ai sau fallback)."""
    try:
        import ai_track
        if ai_track._loop is not None and cam_id in ai_track._loop.cams:
            return ai_track._loop.cams[cam_id]
    except Exception:
        pass
    try:
        import track
        return track.workers.get(cam_id)
    except Exception:
        return None


def snapshot():
    """Starea galeriei pentru /health: {zi, persoane: {nume: nr_embeddings}}."""
    return {"zi": _galerie["zi"],
            "persoane": {n: len(l) for n, l in _galerie["persoane"].items()}}
