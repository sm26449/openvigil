#!/usr/bin/env python3
"""Client subtire pe care nvr-web il foloseste sa vorbeasca cu serviciul nvr-ai.

De la #9 pas 1, pe langa starea de urmarire, aici trec si joburile de modele
(Face ID, LPR, descriere VLM) — web-ul trimite cadrul de fallback (JPEG) si
parametrii, nvr-ai isi trage singur cadrul HD si intoarce rezultatul.

Orice esec -> None, ca apelantul sa cada elegant pe implementarea LOCALA
(modelele se incarca lazy in web doar atunci). `remote()` e comutatorul:
setarea ai_service + serviciul chiar raspunde (health cache-uit 5s).
Contractul complet: docs/ai-contract.md.
"""
import json
import os
import time

import requests
import logare
import config as C
from i18n import t

AI = f"http://127.0.0.1:{C.AI_PORT}"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(ROOT, "data", "ai_token")

_token_cache = {"v": "", "t": 0}
_up_cache = {"ok": False, "t": 0}


def _token():
    now = time.time()
    if now - _token_cache["t"] > 60:
        try:
            with open(TOKEN_FILE) as f:
                _token_cache["v"] = f.read().strip()
        except OSError:
            _token_cache["v"] = ""
        _token_cache["t"] = now
    return _token_cache["v"]


def up(ttl=5):
    """Serviciul raspunde? (cache scurt: nu vrem un health-check per job)."""
    now = time.time()
    if now - _up_cache["t"] > ttl:
        _up_cache.update(ok=health() is not None, t=now)
    return _up_cache["ok"]


def local_permis(nume):
    """In nvr-web, cu `ai_service` pornit, modelele NU se incarca local: nvr-ai le tine, iar
    un al doilea set in web (cand nvr-ai reporneste) umplea VRAM-ul si le dobora pe ambele.
    Ridica RuntimeError (rutele raspund 503) daca serviciul e configurat si nu suntem gazda."""
    import settings
    import detect
    if settings.get("ai_service") and not detect.IS_HOST:
        raise RuntimeError(t("nvr-ai e configurat (ai_service) și nu răspunde — modelul {nume} nu se încarcă în nvr-web",
                             nume=nume))


def remote():
    """Joburile de modele merg la nvr-ai? (comutatorul + serviciul viu)."""
    import settings
    return bool(settings.get("ai_service")) and up()


def _post(path, jpeg=None, timeout=30, **params):
    """POST cu JPEG in corp si parametrii in query. None la orice esec (fallback local)."""
    try:
        params["token"] = _token()
        r = requests.post(f"{AI}{path}", params=params, data=jpeg, timeout=timeout,
                          headers={"Content-Type": "application/octet-stream"})
        if r.ok or 400 <= r.status_code < 500:   # 4xx = raspuns cu motiv (nu fallback local)
            return r.json()
        logare.info(f"[ai-client] {path}: HTTP {r.status_code} {r.text[:80]}")
    except requests.RequestException as e:
        logare.info(f"[ai-client] {path}: {str(e)[:60]}")
    return None


def face_process(cam_id, jpeg):
    """-> lista de nume recunoscute, sau None (esec -> fallback local)."""
    d = _post("/face/process", jpeg, cam=cam_id)
    return None if d is None else d.get("gasiti", [])


def face_known(jpeg):
    """-> {"nume": str|None} sau None la esec (distinge esecul de ne-potrivire)."""
    return _post("/face/known", jpeg)


def face_enroll(name, jpeg):
    return _post("/face/enroll", jpeg, timeout=60, name=name)


def face_auto_enroll():
    return _post("/face/auto_enroll", timeout=180)


def face_regrupeaza():
    return _post("/face/regrupeaza", timeout=180)


def face_uneste(a, b):
    return _post_json("/face/uneste", timeout=120, a=a, b=b)


def face_groups():
    """-> {"grupuri": [...]} sau None (fallback local in ruta)."""
    return _post("/face/unknown_groups", timeout=180)


def reid_cauta(**body):
    """Ca _post_json, dar un 4xx („niciun om in poza”) vine inapoi cu mesajul lui, nu ca esec."""
    try:
        r = requests.post(f"{AI}/reid/cauta", timeout=60, json={"token": _token(), **body})
        if r.ok or 400 <= r.status_code < 500:
            return r.json()
        logare.info(f"[ai-client] /reid/cauta: HTTP {r.status_code} {r.text[:80]}")
    except (requests.RequestException, ValueError) as e:
        logare.info(f"[ai-client] /reid/cauta: {str(e)[:60]}")
    return None


def reid_stare():
    try:
        return requests.get(f"{AI}/reid/stare", params={"token": _token()}, timeout=6).json()
    except (requests.RequestException, ValueError):
        return None


def nesigure_start(n):
    return _post_json("/adnotare/nesigure/start", timeout=20, n=n)


def nesigure_stare():
    try:
        return requests.get(f"{AI}/adnotare/nesigure/stare", params={"token": _token()}, timeout=6).json()
    except (requests.RequestException, ValueError):
        return None


def train_stare():
    """Jobul de antrenament din nvr-ai (None daca serviciul nu raspunde)."""
    try:
        return requests.get(f"{AI}/train/stare", params={"token": _token()}, timeout=6).json()
    except (requests.RequestException, ValueError):
        return None


def train_start(tip, epoci=None, maxim=None):
    return _post_json("/train/start", timeout=20, tip=tip, epoci=epoci, max=maxim)


def train_stop():
    return _post_json("/train/stop", timeout=20)


def face_rename(vechi, nou):
    """-> {ok, nume} sau {error} sau None (fallback local in ruta)."""
    return _post_json("/face/rename", timeout=30, vechi=vechi, nou=nou)


def face_delete(name, photo=None):
    params = {"name": name}
    if photo:
        params["photo"] = photo
    return _post("/face/delete", timeout=60, **params)


def lpr_process(cam_id, jpeg):
    """-> lista de numere citite, sau None (esec -> fallback local)."""
    d = _post("/lpr/process", jpeg, cam=cam_id)
    return None if d is None else d.get("numere", [])


def describe_person(cam_id, jpeg, boxes):
    return _post("/describe/person", jpeg, timeout=10, cam=cam_id,
                 boxes=json.dumps(boxes))


def describe_vehicle(cam_id, jpeg, boxes, moment=None):
    return _post("/describe/vehicle", jpeg, timeout=10, cam=cam_id,
                 boxes=json.dumps(boxes), moment=moment or "")


def detect_state():
    """Starea detectiei din nvr-ai: {detect: {cam(int): {labels, recent}}, gate_ratio}.
    None la esec (apelantul cade pe detect.snapshot() local — gol cand bucla e remote,
    dar UI-ul degradeaza elegant)."""
    try:
        d = requests.get(f"{AI}/detect/state", timeout=6).json()
        d["detect"] = {int(k): v for k, v in (d.get("detect") or {}).items()}
        d["stationare"] = {int(k): v for k, v in (d.get("stationare") or {}).items()}
        return d
    except Exception:
        return None


def detect_confirm(cam_id):
    """Confirmarea de intrus (YOLO + gating Face ID) in nvr-ai.
    -> {gasit, nume, adnotat(b64)?} sau None la esec (fallback local in camctl)."""
    return _post("/detect/confirm", timeout=45, cam=cam_id)


def _post_json(path, timeout=30, **body):
    """POST JSON (jetonul intra in corp). None la orice esec (fallback local)."""
    try:
        r = requests.post(f"{AI}{path}", timeout=timeout,
                          json={"token": _token(), **body})
        if r.ok or 400 <= r.status_code < 500:   # 4xx = raspuns cu motiv (nu fallback local)
            return r.json()
        logare.info(f"[ai-client] {path}: HTTP {r.status_code} {r.text[:80]}")
    except requests.RequestException as e:
        logare.info(f"[ai-client] {path}: {str(e)[:60]}")
    return None


def semantic_index(day, fn):
    """Un snapshot nou (FS comun) intra in indexul din nvr-ai. Best-effort:
    la esec ramane neindexat pana la urmatorul backfill."""
    if _post_json("/semantic/index", timeout=5, day=day, fn=fn) is None:
        logare.error(f"[ai-client] indexare {fn} esuata — intra la backfill")


def semantic_prune():
    _post_json("/semantic/prune", timeout=30)


def semantic_search(q, days, limit, visible):
    """-> lista de rezultate sau None (fallback local)."""
    d = _post_json("/semantic/search", timeout=60, q=q, days=days, limit=limit,
                   visible=None if visible is None else sorted(visible))
    return None if d is None or "results" not in d else d["results"]


def ask(q, visible, cam_names, cam):
    """-> raspunsul lui ask.answer sau None (fallback local)."""
    return _post_json("/ask", timeout=90, q=q,
                      visible=None if visible is None else sorted(visible),
                      cam_names=cam_names, cam=cam)


def semantic_status():
    try:
        return requests.get(f"{AI}/semantic/status", timeout=10).json()
    except Exception:
        return None


def semantic_backfill():
    return _post_json("/semantic/backfill", timeout=30)


def warmup(**kinds):
    """warmup(face=True, lpr=True, describe=True, semantic=True) — preincarca in nvr-ai."""
    try:
        requests.post(f"{AI}/warmup", timeout=5,
                      json={"token": _token(), **kinds})
    except requests.RequestException:
        pass


def capabilities():
    try:
        return requests.get(f"{AI}/capabilities", timeout=6).json()
    except Exception:
        return None


def model_verifica(path):
    """Pre-flight pe un candidat de model in nvr-ai. -> {ok, clase, sha256} sau
    {error: motivul} (422 e un raspuns UTIL, nu un esec de transport) sau None."""
    try:
        r = requests.post(f"{AI}/model/verifica", timeout=120,
                          json={"token": _token(), "path": path})
        return r.json()
    except Exception:
        return None


def model_reincarca():
    """Reincarcare fortata + confirmare in nvr-ai. -> {ok, amprenta, clase} /
    {error} / None."""
    try:
        r = requests.post(f"{AI}/model/reincarca", timeout=120,
                          json={"token": _token()})
        return r.json()
    except Exception:
        return None


def metrici():
    """Metricile per model din nvr-ai (faza 6), sau None."""
    try:
        return requests.get(f"{AI}/metrici", timeout=6).json()
    except Exception:
        return None


def track_state():
    try:
        return requests.get(f"{AI}/state", timeout=6).json()
    except Exception:
        return None


def health():
    try:
        return requests.get(f"{AI}/health", timeout=2).json()
    except Exception:
        return None
