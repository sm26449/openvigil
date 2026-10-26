#!/usr/bin/env python3
"""Recunoastere persoane (Face ID) — cine e omul din cadru, nu doar ca e un om.

Motor: InsightFace buffalo_l (detector SCRFD + embeddings ArcFace 512-dim) pe ONNX
Runtime, local, pe GPU cand exista (fallback CPU) — inferenta ruleaza DOAR cand YOLO
tocmai a confirmat un om,
deci costul e per eveniment, nu per cadru.

Fluxul: detect.py vede "person" -> cerem camerei un cadru la rezolutie mare (ISAPI
picture, mainstream; substreamul de 640px face fetele de la cativa metri inutilizabile)
-> cautam fete -> comparam cu biblioteca (similaritate cosinus). Peste prag = persoana
recunoscuta: snapshot cu numele ei, notificare, webhook "persoana". Sub prag = fata
necunoscuta: crop-ul se pastreaza in data/faces/unknown/ pentru etichetare din UI —
etichetarea e chiar inrolarea, asa creste biblioteca.

Biblioteca: data/faces/persons/<nume>/<fisier>.jpg (crop-uri de fata, vizibile in UI)
+ data/faces/embeddings.json (cache-ul vectorilor, reconstruit la orice schimbare).
Numele sunt folosite ca etichete de snapshot, deci raman ASCII simplu ([a-z0-9-]).
"""
import json
import os
import re
import threading
import time
import unicodedata

import numpy as np
import requests
from requests.auth import HTTPDigestAuth

import settings
import store
import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FACES_DIR = os.path.join(ROOT, "data", "faces")
PERSONS_DIR = os.path.join(FACES_DIR, "persons")
UNKNOWN_DIR = os.path.join(FACES_DIR, "unknown")
EMB_FILE = os.path.join(FACES_DIR, "embeddings.json")
MODEL_ROOT = os.path.join(FACES_DIR, "insightface")
LOG_FILE = os.path.join(FACES_DIR, "log.jsonl")
LOG_MAX = 20000

UNIFACE_DIR = os.path.join(FACES_DIR, "uniface")
MIN_FACE = 40          # px latura minima a fetei; sub atat embedding-ul e nesigur
MIN_DET_SCORE = 0.55   # scorul detectorului de fete sub care ignoram complet
CROP_MARGIN = 0.35     # cat context pastram in jurul fetei in crop-urile salvate
UNKNOWN_COOLDOWN = 60  # secunde intre doua crop-uri necunoscute salvate per camera
UNKNOWN_MAX = 500      # plafonul de fisiere din unknown/ (cele mai vechi se sterg)

_app = None
_init_lock = threading.Lock()      # incarcarea modelului: un singur thread o face
_lock = threading.Lock()           # serializarea inferentelor
_emb_cache = {"mtime": None, "data": {}}
_unknown_last = {}


def enabled():
    return bool(settings.get("face_enabled"))


def gpu_providers():
    """(provideri ONNX, ctx_id) — CUDA cand exista, altfel CPU. Importul torch nu e
    decorativ: el incarca librariile cuDNN din pachetele nvidia-*, fara de care
    providerul CUDA al onnxruntime nu porneste."""
    try:
        import torch
        if torch.cuda.is_available():
            return ["CUDAExecutionProvider", "CPUExecutionProvider"], 0
    except Exception:
        pass
    return ["CPUExecutionProvider"], -1


def _get_app():
    """Modelul, incarcat lenes (prima folosire dureaza cateva secunde). Lock separat
    de cel de inferenta: warmup-ul si prima detectie nu au voie sa-l incarce amandoua."""
    import ai_client
    ai_client.local_permis("Face ID")
    global _app
    with _init_lock:
        if _app is None:
            from insightface.app import FaceAnalysis
            prov, ctx = gpu_providers()
            a = FaceAnalysis(name="buffalo_l", root=MODEL_ROOT, providers=prov)
            a.prepare(ctx_id=ctx, det_size=(640, 640))
            _app = a
            logare.info(f"[faces] model buffalo_l incarcat "
                  f"({'GPU' if ctx >= 0 else 'CPU'})")
    return _app


def warmup_async():
    """Incarca modelul in fundal la pornirea serverului, ca prima detectie reala sa nu
    astepte incarcarea."""
    if not enabled():
        return
    threading.Thread(target=lambda: _get_app(), daemon=True).start()


def sanitize_name(name):
    """Nume de persoana -> eticheta sigura de fisier/snapshot: ASCII mic, [a-z0-9-]."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower()).strip("-")
    if not re.match(r"^[a-z][a-z0-9-]{1,19}$", s):
        raise ValueError(t("nume invalid (2-20 caractere, litere/cifre/liniuta, incepe cu litera)"))
    return s


# ---- biblioteca (persoane + embeddings) ----
def _load_embeddings():
    """Cache-ul de embeddings, reincarcat doar cand fisierul s-a schimbat pe disc."""
    try:
        mtime = os.path.getmtime(EMB_FILE)
    except FileNotFoundError:
        return {}
    if _emb_cache["mtime"] != mtime:
        try:
            with open(EMB_FILE) as f:
                raw = json.load(f)
            _emb_cache["data"] = {n: np.array(v, dtype=np.float32) for n, v in raw.items()
                                  if v}
            _emb_cache["mtime"] = mtime
        except (ValueError, OSError):
            return _emb_cache["data"]
    return _emb_cache["data"]


def _save_embeddings(data):
    import store
    store.atomic_write_json(EMB_FILE, {n: [list(map(float, e)) for e in v] for n, v in data.items()},
                            mode=0o600)
    _emb_cache["mtime"] = None   # urmatoarea citire reincarca


def persons():
    """[{name, photos}] — cine e in biblioteca si cu cate poze."""
    out = []
    if os.path.isdir(PERSONS_DIR):
        for name in sorted(os.listdir(PERSONS_DIR)):
            d = os.path.join(PERSONS_DIR, name)
            if os.path.isdir(d):
                pics = sorted(f for f in os.listdir(d) if f.endswith(".jpg"))
                out.append({"name": name, "photos": pics})
    return out


def match(embedding, threshold=None):
    """Cea mai apropiata persoana din biblioteca: (nume, similaritate) sau (None, best)."""
    if threshold is None:
        threshold = float(settings.get("face_threshold"))
    lib = _load_embeddings()
    best_name, best_sim = None, -1.0
    for name, embs in lib.items():
        sim = float(np.max(embs @ embedding)) if len(embs) else -1.0
        if sim > best_sim:
            best_name, best_sim = name, sim
    if best_sim >= threshold:
        return best_name, best_sim
    return None, best_sim


def known_face(img):
    """Numele unei persoane CUNOSCUTE (din biblioteca) gasite in cadru, sau None.
    Pentru gating-ul de intrus: o fata de familie nu e intrus. None si daca Face ID
    e oprit, biblioteca e goala, sau fata e prea departe/intoarsa ca sa fie citita."""
    if not enabled() or not _load_embeddings():
        return None                 # oprit sau biblioteca goala -> nimeni cunoscut
    try:
        import metrici
        with metrici.cronometru("face"):
            found = _detect(img)
    except Exception:
        return None
    for f in found:
        name, _ = match(f.normed_embedding)
        if name:
            return name
    return None


def _rebuild_person(name):
    """Recalculeaza embeddings pentru o persoana din pozele ei de pe disc."""
    d = os.path.join(PERSONS_DIR, name)
    import cv2
    embs = []
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith(".jpg"):
            continue
        img = cv2.imread(os.path.join(d, fn))
        if img is None:
            continue
        faces = _detect(img)
        if faces:
            # cea mai mare fata din poza — poza de inrolare are o singura fata relevanta
            f = max(faces, key=lambda x: _face_size(x))
            embs.append(f.normed_embedding)
    data = _load_embeddings().copy()
    if embs:
        data[name] = np.stack(embs)
    else:
        data.pop(name, None)
    _save_embeddings(data)
    return len(embs)


def _face_size(f):
    x1, y1, x2, y2 = f.bbox
    return min(x2 - x1, y2 - y1)


def _detect(img_bgr):
    """Toate fetele utilizabile dintr-un cadru (marime + scor minim)."""
    with _lock:
        found = _get_app().get(img_bgr)
    return [f for f in found
            if _face_size(f) >= MIN_FACE and f.det_score >= MIN_DET_SCORE]


def _crop(img, bbox):
    h, w = img.shape[:2]
    x1, y1, x2, y2 = bbox
    mx, my = (x2 - x1) * CROP_MARGIN, (y2 - y1) * CROP_MARGIN
    x1, y1 = max(0, int(x1 - mx)), max(0, int(y1 - my))
    x2, y2 = min(w, int(x2 + mx)), min(h, int(y2 + my))
    return img[y1:y2, x1:x2]


def enroll(name, jpeg_bytes):
    """Adauga o poza la o persoana (o creeaza daca nu exista). Poza trebuie sa contina
    o fata utilizabila; se pastreaza crop-ul fetei, nu poza intreaga."""
    import cv2
    name = sanitize_name(name)
    img = cv2.imdecode(np.frombuffer(jpeg_bytes, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(t("imaginea nu poate fi citita"))
    faces = _detect(img)
    if not faces:
        raise ValueError(t("nicio față utilizabila în imagine (prea mica sau neclara)"))
    f = max(faces, key=_face_size)
    d = os.path.join(PERSONS_DIR, name)
    os.makedirs(d, exist_ok=True)
    fn = time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000}.jpg"
    ok, buf = cv2.imencode(".jpg", _crop(img, f.bbox), [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise ValueError(t("nu am putut salva crop-ul"))
    with open(os.path.join(d, fn), "wb") as fh:
        fh.write(buf.tobytes())
    n = _rebuild_person(name)
    return {"name": name, "photo": fn, "embeddings": n}


_unk_cache = {}        # cale -> (mtime, embedding) — crop-urile necunoscute nu se re-embed


def _unknown_embeddings():
    """Embedding-ul fiecarui crop din unknown/ (cel mai mare chip din poza), cu cache pe
    mtime: gruparea se poate cere oricand din interfata fara sa reproceseze tot.
    Intoarce (E: array N x d, meta: [(zi, rel, cale)])."""
    import glob
    import cv2
    files = sorted(glob.glob(os.path.join(UNKNOWN_DIR, "*", "*.jpg")))
    embs, meta = [], []
    for f in files:
        try:
            mt = os.path.getmtime(f)
        except OSError:
            continue
        c = _unk_cache.get(f)
        if c and c[0] == mt:
            emb = c[1]
        else:
            img = cv2.imread(f)
            det = _detect(img) if img is not None else None
            emb = max(det, key=_face_size).normed_embedding if det else None
            _unk_cache[f] = (mt, emb)
        if emb is None:
            continue
        embs.append(emb)
        zi = os.path.basename(os.path.dirname(f))
        meta.append((zi, f"unknown/{zi}/{os.path.basename(f)}", f))
    for f in [f for f in _unk_cache if f not in set(files)]:
        _unk_cache.pop(f, None)
    return (np.array(embs) if embs else np.zeros((0, 512))), meta


def _cluster(E, sim_thr):
    """Grupare lacoma pe similaritate cosinus: un crop intra in primul grup in care are
    un vecin peste prag, altfel deschide un grup nou. Intoarce liste de indici."""
    clusters = []
    for i in range(len(E)):
        for cl in clusters:
            if float(np.max(E[cl] @ E[i])) >= sim_thr:
                cl.append(i)
                break
        else:
            clusters.append([i])
    return clusters


def unknown_groups(sim_thr=None, min_size=2, max_samples=6, cu_centroid=False):
    """Fetele necunoscute grupate pe persoana (fara sa inroleze nimic): pentru fiecare grup
    cu cel putin `min_size` poze — cate poze, in cate zile, cateva mostre si, daca seamana
    cu cineva din biblioteca, sugestia (nume + similaritate). Utilizatorul confirma:
    'e X' / 'persoana noua' / 'ignora'. Asa biblioteca creste din ce s-a capturat deja,
    fara dublari si fara amestecarea persoanelor (ce a patit inrolarea automata)."""
    if sim_thr is None:
        sim_thr = float(settings.get("face_threshold"))
    E, meta = _unknown_embeddings()
    if not len(E):
        return []
    lib = _load_embeddings()
    out = []
    for gi, cl in enumerate(sorted(_cluster(E, sim_thr), key=len, reverse=True)):
        if len(cl) < min_size:
            continue
        centroid = E[cl].mean(axis=0)
        centroid /= (np.linalg.norm(centroid) or 1.0)
        best, best_sim = None, -1.0
        for name, embs in lib.items():
            if len(embs):
                s = float(np.max(embs @ centroid))
                if s > best_sim:
                    best, best_sim = name, s
        zile = sorted({meta[i][0] for i in cl})
        # mostrele: distribuite in timp (prima, ultima, si din mijloc), nu doar cele mai noi
        idx = sorted(cl, key=lambda i: meta[i][1])
        pas = max(1, len(idx) // max_samples)
        mostre = [meta[i][1] for i in idx[::pas]][:max_samples]
        g = {"id": gi, "n": len(cl), "zile": len(zile),
             "interval": f"{zile[0]}..{zile[-1]}" if zile else "",
             "poze": [meta[i][1] for i in idx], "mostre": mostre,
             "sugestie": ({"nume": best, "sim": round(best_sim, 3)}
                          if best and best_sim >= 0.35 else None)}
        if cu_centroid:
            g["centroid"] = [round(float(x), 5) for x in centroid]
        out.append(g)
    return out


GRUPURI_FILE = os.path.join(FACES_DIR, "grupuri.json")
_regrup = {"timer": None, "lock": threading.Lock(), "tlock": threading.Lock()}
PRAG_ID_STABIL = 0.60      # un grup nou „e” un grup vechi daca centroizii seamana peste atat
PRAG_PERECHE = 0.50        # doua persoane din biblioteca „par aceeasi” peste atat


def grupuri():
    """Grupurile permanente de fete necunoscute (fisierul scris de regrupeaza())."""
    return store.load_json(GRUPURI_FILE, {"grupuri": {}, "perechi": [], "contor": 0, "t": None}) \
        or {"grupuri": {}, "perechi": [], "contor": 0, "t": None}


def potriveste_ids(centroizi, vechi, contor):
    """Id-uri stabile intre doua regrupari: fiecare grup nou ia id-ul grupului vechi cu care
    seamana cel mai mult (cosinus >= PRAG_ID_STABIL, un id o singura data), altfel unul nou.
    centroizi: [np.array], vechi: {gid: centroid list}. Intoarce ([gid...], contor_nou)."""
    ids, folosite = [], set()
    v_ids = list(vechi)
    V = np.array([vechi[g] for g in v_ids]) if v_ids else np.zeros((0, 512))
    for c in centroizi:
        best, bs = None, PRAG_ID_STABIL
        if len(V):
            sims = V @ c
            for j in np.argsort(-sims):
                if v_ids[j] in folosite:
                    continue
                if float(sims[j]) >= bs:
                    best, bs = v_ids[j], float(sims[j])
                break
        if best is None:
            contor += 1
            best = f"nec-{contor}"
        folosite.add(best)
        ids.append(best)
    return ids, contor


def _ultima_aparitie(poze):
    """Din numele crop-urilor (unknown/<zi>/<HHMMSS>_cam<id>.jpg): cea mai noua aparitie."""
    best = None
    for rel in poze:
        m = re.match(r"^unknown/(\d{4}-\d{2}-\d{2})/(\d{6})_cam(\d+)\.jpg$", rel)
        if m:
            k = (m[1], m[2])
            if best is None or k > best[:2]:
                best = (m[1], m[2], int(m[3]))
    return {"zi": best[0], "ora": f"{best[1][:2]}:{best[1][2:4]}:{best[1][4:]}", "cam": best[2]} if best else None


def perechi_persoane():
    """Perechi de persoane din biblioteca care par aceeasi (max similaritate >= PRAG_PERECHE)."""
    lib = _load_embeddings()
    nume = sorted(k for k, v in lib.items() if len(v))
    out = []
    for i, a in enumerate(nume):
        for b in nume[i + 1:]:
            s = float(np.max(lib[a] @ lib[b].T))
            if s >= PRAG_PERECHE:
                out.append({"a": a, "b": b, "sim": round(s, 3)})
    return sorted(out, key=lambda p: -p["sim"])


def regrupeaza(sim_thr=None):
    """Regrupeaza fetele necunoscute si scrie grupuri.json cu id-uri stabile, ultima aparitie,
    sugestia din biblioteca si perechile de persoane care par aceeasi. Ieftin dupa prima
    rulare (embedding-urile crop-urilor sunt in cache)."""
    with _regrup["lock"]:
        vechi = grupuri()
        gr = unknown_groups(sim_thr=sim_thr, min_size=2, cu_centroid=True)
        ids, contor = potriveste_ids([np.array(g["centroid"]) for g in gr],
                                     {k: v["centroid"] for k, v in vechi.get("grupuri", {}).items() if v.get("centroid")},
                                     int(vechi.get("contor") or 0))
        noi = {}
        for gid, g in zip(ids, gr):
            g.pop("id", None)
            g["ultima"] = _ultima_aparitie(g["poze"])
            noi[gid] = g
        data = {"grupuri": noi, "perechi": perechi_persoane(), "contor": contor,
                "t": time.strftime("%Y-%m-%d %H:%M:%S")}
        os.makedirs(FACES_DIR, exist_ok=True)
        store.atomic_write_json(GRUPURI_FILE, data, mode=0o600)
        return data


def programeaza_regrupare(dupa=120):
    """Dupa un crop nou: o regrupare peste `dupa` secunde (una singura, oricate crop-uri vin)."""
    with _regrup["tlock"]:          # NU lacatul regruparii (ar bloca firul de detectie un minut)
        if _regrup["timer"] is not None:
            return
        def _run():
            _regrup["timer"] = None
            try:
                regrupeaza()
            except Exception as e:
                logare.error(f"[faces] regruparea a esuat: {str(e)[:120]}")
        t = threading.Timer(dupa, _run)
        t.daemon = True
        _regrup["timer"] = t
        t.start()


def uneste(a, b):
    """Persoana `b` intra in `a` (pozele se muta, embedding-urile se refac, `b` dispare)."""
    a, b = sanitize_name(a), sanitize_name(b)
    da, db = os.path.join(PERSONS_DIR, a), os.path.join(PERSONS_DIR, b)
    if a == b or not os.path.isdir(da) or not os.path.isdir(db):
        raise ValueError(t("persoane necunoscute sau identice"))
    import shutil
    mutate = 0
    for fn in sorted(os.listdir(db)):
        if not fn.endswith(".jpg"):
            continue
        dst = os.path.join(da, fn)
        if os.path.exists(dst):
            dst = os.path.join(da, fn[:-4] + f"-{b}.jpg")
        shutil.move(os.path.join(db, fn), dst)
        mutate += 1
    shutil.rmtree(db, ignore_errors=True)
    data = _load_embeddings().copy()
    data.pop(b, None)
    _save_embeddings(data)
    n = _rebuild_person(a)
    try:
        regrupeaza()
    except Exception:
        pass
    return {"nume": a, "poze_mutate": mutate, "embeddings": n}


def auto_enroll_residents(min_photos=4, min_days=2, sim_thr=0.45):
    """Foloseste fetele necunoscute deja capturate: le grupeaza pe similaritate si
    inroleaza automat clusterele FRECVENTE (apar des, in mai multe zile = rezidenti/
    familie, nu intrusi) ca 'Rezident N'. Asa gating-ul de intrus ii suprima fara
    inrolare manuala. Utilizatorul poate redenumi/sterge dupa. Intoarce lista inrolatilor.
    Preferabil: unknown_groups() + confirmare din interfata (asta a amestecat persoane)."""
    E, meta_full = _unknown_embeddings()
    meta = [(zi, cale) for zi, _rel, cale in meta_full]
    if len(E) < min_photos:
        return []
    clusters = _cluster(E, sim_thr)
    existing = _load_embeddings()
    n = sum(1 for k in existing if k.startswith("Rezident"))
    enrolled = []
    for cl in sorted(clusters, key=len, reverse=True):
        zile = sorted({meta[i][0] for i in cl})
        if len(cl) < min_photos or len(zile) < min_days:
            continue
        n += 1
        name = f"Rezident {n}"
        poze = 0
        for i in cl:
            try:
                with open(meta[i][1], "rb") as fh:
                    enroll(name, fh.read())
                poze += 1
            except Exception as e:
                logare.info(f"[faces] {name}: poza {meta[i][1]} sarita la reconstruire: "
                      f"{str(e)[:60]}")
        if poze:
            enrolled.append({"nume": name, "poze": poze,
                             "zile": len(zile), "interval": f"{zile[0]}..{zile[-1]}"})
    return enrolled


def rename_person(vechi, nou):
    """Redenumeste o persoana: directorul de poze + cheia din embeddings, atomic
    fata de cititori (embeddings se rescrie complet). Nu cere modelul, dar
    scrierea bibliotecii ramane la gazda (nvr-ai) — un singur scriitor.
    Istoricul (log.jsonl) pastreaza numele vechi la aparitiile de dinainte."""
    vechi = sanitize_name(vechi)
    nou = sanitize_name(nou)
    d_vechi = os.path.join(PERSONS_DIR, vechi)
    d_nou = os.path.join(PERSONS_DIR, nou)
    if not os.path.isdir(d_vechi):
        raise ValueError(t("persoana '{nume}' nu există", nume=vechi))
    if os.path.exists(d_nou) or nou in _load_embeddings():
        raise ValueError(t("numele '{nume}' există deja", nume=nou))
    os.rename(d_vechi, d_nou)
    data = _load_embeddings().copy()
    if vechi in data:
        data[nou] = data.pop(vechi)
        _save_embeddings(data)
    return nou


def delete_person(name, photo=None):
    """Sterge o persoana intreaga sau doar una din pozele ei."""
    name = sanitize_name(name)
    d = os.path.join(PERSONS_DIR, name)
    if not os.path.isdir(d):
        raise ValueError(t("persoana nu există"))
    if photo:
        if not re.match(r"^[\w.-]+\.jpg$", photo):
            raise ValueError(t("nume de fișier invalid"))
        p = os.path.join(d, photo)
        if not os.path.isfile(p):
            raise ValueError(t("poza nu există"))
        os.remove(p)
        if not any(f.endswith(".jpg") for f in os.listdir(d)):
            os.rmdir(d)
    else:
        import shutil
        shutil.rmtree(d)
    _rebuild_person(name)


# ---- anti-spoofing (o poza sau un ecran aratat camerei nu e o persoana) ----
# Ansamblul standard MiniFASNet: V1SE (scala 4.0) + V2 (scala 2.7), ca in
# Silent-Face-Anti-Spoofing / DeepFace. Rulaza DOAR la recunoastere — inrolarea
# din poze incarcate e legitima prin natura ei si nu trece prin verificare.
_spoof_models = None


def _get_spoof():
    global _spoof_models
    with _init_lock:
        if _spoof_models is None:
            import uniface
            uniface.set_cache_dir(UNIFACE_DIR)
            from uniface import MiniFASNet
            from uniface.constants import MiniFASNetWeights
            prov, _ = gpu_providers()
            _spoof_models = [MiniFASNet(MiniFASNetWeights.V1SE, providers=list(prov)),
                             MiniFASNet(MiniFASNetWeights.V2, providers=list(prov))]
            logare.info("[faces] anti-spoofing MiniFASNet incarcat")
    return _spoof_models


def combine_spoof(results, min_conf):
    """Verdictul ansamblului, pur si testabil. results = [(is_real, confidence), ...].

    Respingem DOAR cand ambele modele spun "fals" si media increderii trece pragul —
    o fata reala vazuta prost (departe, intuneric) nu trebuie sa piarda recunoasterea."""
    if not results:
        return True, 0.0
    fake = [c for real, c in results if not real]
    conf = sum(c for _, c in results) / len(results)
    if len(fake) == len(results) and conf >= min_conf:
        return False, conf
    return True, conf


def spoof_check(img, bbox):
    """(e_reala, incredere) pentru o fata din cadru."""
    box = [int(v) for v in bbox]
    rez = []
    with _lock:
        for m in _get_spoof():
            r = m.predict(img, box)
            rez.append((bool(r.is_real), float(r.confidence)))
    return combine_spoof(rez, float(settings.get("face_antispoof_conf") or 0.7))


# ---- jurnalul aparitiilor ("a mai venit pe la noi?") ----
_seen_emit = {}
SEEN_COOLDOWN = 60


def emit_ok(cam_id, nume, now=None):
    """Re-recunoasterea periodica (badge-ul viu) nu are voie sa umple jurnalul si
    notificarile: aceeasi persoana pe aceeasi camera se consemneaza o data pe minut."""
    now = now or time.time()
    if now - _seen_emit.get((cam_id, nume), 0) < SEEN_COOLDOWN:
        return False
    _seen_emit[(cam_id, nume)] = now
    return True


def _log_seen(cam_id, nume, sim):
    try:
        os.makedirs(FACES_DIR, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"),
                                "cam": cam_id, "nume": nume,
                                "sim": round(float(sim), 3)}) + "\n")
        if sum(1 for _ in open(LOG_FILE)) > LOG_MAX:
            import store
            store.atomic_write_text(LOG_FILE, "".join(open(LOG_FILE).readlines()[-LOG_MAX // 2:]))
    except OSError:
        pass


def history(nume=None, limit=100):
    """Aparitiile recunoscute, cele mai noi primele — istoricul unei persoane sau al
    tuturor. De aici se raspunde la "cand a mai venit X pe la noi?"."""
    try:
        with open(LOG_FILE) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in reversed(lines):
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        if nume and e.get("nume") != nume:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out


# ---- fete necunoscute (materia prima pentru inrolare) ----
def _prune_unknown():
    files = []
    for day in (os.listdir(UNKNOWN_DIR) if os.path.isdir(UNKNOWN_DIR) else []):
        dd = os.path.join(UNKNOWN_DIR, day)
        if os.path.isdir(dd):
            files += [os.path.join(dd, f) for f in os.listdir(dd)]
    files.sort(key=os.path.getmtime)
    for p in files[:-UNKNOWN_MAX] if len(files) > UNKNOWN_MAX else []:
        os.remove(p)


def save_unknown(cam_id, img, bbox):
    now = time.time()
    if now - _unknown_last.get(cam_id, 0) < UNKNOWN_COOLDOWN:
        return None
    _unknown_last[cam_id] = now
    import cv2
    day = time.strftime("%Y-%m-%d")
    d = os.path.join(UNKNOWN_DIR, day)
    os.makedirs(d, exist_ok=True)
    fn = f"{time.strftime('%H%M%S')}_cam{cam_id}.jpg"
    ok, buf = cv2.imencode(".jpg", _crop(img, bbox), [cv2.IMWRITE_JPEG_QUALITY, 92])
    if ok:
        with open(os.path.join(d, fn), "wb") as fh:
            fh.write(buf.tobytes())
        _prune_unknown()
        return f"unknown/{day}/{fn}"
    return None


def unknown_list(limit=100):
    """Cele mai noi crop-uri neetichetate: [{rel, cam, time}]."""
    out = []
    for day in sorted(os.listdir(UNKNOWN_DIR), reverse=True) if os.path.isdir(UNKNOWN_DIR) else []:
        dd = os.path.join(UNKNOWN_DIR, day)
        if not os.path.isdir(dd):
            continue
        for fn in sorted(os.listdir(dd), reverse=True):
            m = re.match(r"^(\d{6})_cam(\d+)\.jpg$", fn)
            if m:
                out.append({"rel": f"unknown/{day}/{fn}", "cam": int(m[2]),
                            "time": f"{day} {m[1][:2]}:{m[1][2:4]}:{m[1][4:]}"})
            if len(out) >= limit:
                return out
    return out


def delete_unknown(rel=None):
    """Sterge un crop necunoscut (rel) sau toate."""
    import shutil
    if rel is None:
        if os.path.isdir(UNKNOWN_DIR):
            shutil.rmtree(UNKNOWN_DIR)
        return
    p = safe_path(rel)
    if p and os.path.isfile(p):
        os.remove(p)


def safe_path(rel):
    """data/faces/<rel> doar daca ramane in interiorul directorului (fara ..)."""
    p = os.path.normpath(os.path.join(FACES_DIR, rel))
    return p if p.startswith(FACES_DIR + os.sep) else None


# ---- cadrul HD + fluxul per detectie ----
def hd_frame(cam):
    """Cadru mainstream direct de pe camera (ISAPI picture) — fetele au nevoie de
    rezolutie; substreamul de 640px nu ajunge. La esec intoarce None (folosim
    cadrul de substream deja avut)."""
    try:
        # rezolutia NATIVA a mainstream-ului (4-8MP): la 1920 fetele din curte ieseau sub
        # pragul de 40px. Poza vine prin driver (ISAPI picture sau ONVIF GetSnapshotUri).
        import drivers
        jpeg = drivers.snapshot(cam)
        if jpeg:
            import cv2
            return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        pass
    return None


def process(cam_id, sub_img, cam_entry):
    """Ruleaza Face ID pentru o detectie de persoana. Intoarce numele recunoscute.

    Se apeleaza din workerul de detectie DOAR cand "person" tocmai a fost confirmat,
    deci de cateva ori pe ora, nu de cateva ori pe secunda."""
    if not enabled():
        return []
    img = None
    if cam_entry and cam_entry.get("user") and cam_entry.get("pass"):
        img = hd_frame(cam_entry)
    if img is None:
        img = sub_img
    if img is None:
        return []
    try:
        import metrici
        with metrici.cronometru("face"):
            found = _detect(img)
    except Exception as e:
        logare.error(f"[faces] cam{cam_id}: inferenta esuata: {str(e)[:80]}")
        return []
    names = []
    consemnate = []
    for f in found:
        if settings.get("face_antispoof"):
            try:
                reala, conf = spoof_check(img, f.bbox)
            except Exception as e:
                logare.error(f"[faces] cam{cam_id}: anti-spoofing esuat: {str(e)[:80]}")
                reala = True
            if not reala:
                # o poza/un ecran aratat camerei: nu potrivim, nu inrolam — dar
                # anuntam, pentru ca tentativa in sine e un semnal de securitate
                logare.warning(f"[faces] cam{cam_id}: fata FALSA respinsa "
                      f"(incredere {conf:.2f})")
                try:
                    import hooks
                    hooks.emit("fata_falsa", cam_id, incredere=round(conf, 3))
                except Exception:
                    pass
                continue
        name, sim = match(f.normed_embedding)
        if name:
            names.append((name, sim, f.bbox))
            try:
                import reid
                # fata ancoreaza corpul: din acelasi cadru (HD) + trackul viu
                reid.ancoreaza(cam_id, name, img=img, bbox=f.bbox)
            except Exception:
                pass
            if emit_ok(cam_id, name):
                consemnate.append(name)
                _log_seen(cam_id, name, sim)
        else:
            rel = (save_unknown(cam_id, img, f.bbox)
                   if settings.get("face_unknown_save") else None)
            if rel:   # eveniment doar cand chiar am pastrat crop-ul (respecta cooldown-ul)
                programeaza_regrupare()   # necunoscutii apar ca persoane fara sa ceara nimeni
                try:
                    import hooks
                    hooks.emit("persoana_necunoscuta", cam_id,
                               similaritate_maxima=round(float(sim), 3), crop=rel)
                except Exception:
                    pass
    if names:
        import cv2
        import snaps
        import notify
        for name, sim, bbox in names:
            x1, y1, x2, y2 = (int(v) for v in bbox)
            cv2.rectangle(img, (x1, y1), (x2, y2), (80, 200, 120), 2)
            cv2.putText(img, f"{name} {sim:.2f}", (x1, max(20, y1 - 8)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 200, 120), 2)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 88])
        jpeg = buf.tobytes() if ok else None
        for name, sim, _ in names:
            if name not in consemnate:
                continue   # re-recunoastere sub minutul de racord: doar badge-ul viu
            snaps.save_frame(cam_id, name, img)
            notify.face(cam_id, name, jpeg)
            try:
                import hooks
                hooks.emit("persoana", cam_id, nume=name, similaritate=round(sim, 3))
            except Exception:
                pass
        logare.info(f"[faces] cam{cam_id}: recunoscut "
              + ", ".join(f"{n} ({s:.2f})" for n, s, _ in names))
    return [n for n, _, _ in names]
