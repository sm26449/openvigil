#!/usr/bin/env python3
"""Regula de cădere — orchestrarea (partea nvr-ai). Leagă percepția (pose) de logica pură
(reguli.scor_cadere + dwell), confirmă cu VLM și, la declanșare, emite un eveniment `cadere`.
Politica și escaladarea (incident critic, Telegram, alertd) le face nvr-web pe evenimentul ăsta
— vezi docs/reguli-analiza.md.

Pose și VLM sunt INJECTATE (`keypoints_fn`, `confirm_fn`), ca orchestratorul să fie testabil
fără model. În viață le dau `cadere_pose.keypoints` și o întrebare la Qwen.
"""
import re
import threading
import time

import settings
import logare
import reguli

POSE_INTERVAL = 0.7      # pose cel mult o dată la atâtea secunde per track (dwell nu cere mai des)
PROMPT_CADERE = ("Imagine de la o cameră de supraveghere. Persoana din imagine a căzut și stă "
                 "întinsă sau prăbușită pe jos (NU doar aplecată, așezată sau ghemuită)? "
                 "Răspunde doar cu: da sau nu.")
_last_pose = {}          # (cam, track) -> ultima rulare pose (throttle)

_stare = None     # reguli.Cadere, (re)construit când se schimbă pragurile din Setări
_params = None


def reset():
    """Uită starea (pentru teste și la schimbarea pragurilor)."""
    global _stare, _params
    _stare, _params = None, None


def _reload():
    global _stare, _params
    p = {k: settings.get(k) for k in ("cadere_scor_prag", "cadere_dwell_s", "cadere_pose_conf",
                                      "cadere_persoana_min_px", "cadere_vlm")}
    if _stare is None or p != _params:
        _stare = reguli.Cadere(dwell_s=float(p["cadere_dwell_s"]),
                               scor_prag=float(p["cadere_scor_prag"]))
        _params = p
    return p


def _destul_de_mare(bbox, min_px):
    x1, y1, x2, y2 = bbox
    return min(x2 - x1, y2 - y1) >= min_px


def verifica(cam_id, img, bbox, track_id, now=None, *, keypoints_fn, confirm_fn=None, emit_fn=None):
    """Un pas de verificare pentru o persoană urmărită (bbox în coordonatele cadrului).
    Întoarce dict-ul evenimentului `cadere` dacă s-a declanșat (dwell împlinit + confirmare VLM),
    altfel None. Nu ridică niciodată pe calea VLM — o cădere reală nu se pierde dacă VLM pică.
    """
    now = time.time() if now is None else now
    p = _reload()
    if not _destul_de_mare(bbox, float(p["cadere_persoana_min_px"])):
        return None                       # prea mică/departe: nu o urmărim pentru cădere
    kps = keypoints_fn(img, bbox)
    if not kps:
        return None
    scor = reguli.scor_cadere(kps, bbox, conf_min=float(p["cadere_pose_conf"]))
    if not _stare.update(cam_id, track_id, scor, now):
        return None                       # încă în dwell / s-a ridicat / deja emis în episod
    if p["cadere_vlm"] and confirm_fn is not None:
        try:
            if not confirm_fn(img, bbox):
                logare.info(f"[cadere] cam{cam_id}: dwell împlinit dar VLM a infirmat")
                return None
        except Exception as e:
            logare.info(f"[cadere] cam{cam_id}: VLM eșuat, las să treacă: {str(e)[:60]}")
    ev = {"cam": int(cam_id), "scor": round(float(scor), 3),
          "t": time.strftime("%Y-%m-%d %H:%M:%S")}
    if emit_fn:
        try:
            emit_fn(ev)
        except Exception as e:
            logare.info(f"[cadere] cam{cam_id}: emit eșuat: {str(e)[:60]}")
    logare.info(f"[cadere] cam{cam_id}: CĂDERE (scor {scor:.2f}, track {track_id})")
    return ev


def expire(now=None):
    """Curăță track-urile plecate din cadru (apelat periodic din bucla vie)."""
    if _stare is not None:
        _stare.expire(time.time() if now is None else now)


# ---- glue-ul live (rulat din bucla de tracking, nvr-ai) ----
def pas_pe_cadru(cam_id, frame, persoane, now, emit_fn):
    """Un pas pentru o cameră cu `cadere` pornit. `persoane` = [(track_id, bbox)] (bbox în
    coordonatele cadrului). Pose-ul rulează throttled per track (ieftin, inline); confirmarea VLM
    și emiterea se fac în FUNDAL (VLM durează secunde — nu blocăm bucla de tracking)."""
    import cadere_pose
    declansate = []
    for tid, bbox in persoane:
        k = (int(cam_id), tid)
        if now - _last_pose.get(k, float("-inf")) < POSE_INTERVAL:
            continue
        _last_pose[k] = now
        ev = verifica(cam_id, frame, bbox, tid, now=now, keypoints_fn=cadere_pose.keypoints)
        if ev:
            declansate.append((bbox, ev))
    for k in [k for k, ultim in _last_pose.items() if now - ultim > 60]:
        _last_pose.pop(k, None)
    expire(now)
    for bbox, ev in declansate:
        threading.Thread(target=confirma_si_emite, args=(cam_id, frame.copy(), bbox, ev, emit_fn),
                         daemon=True, name="cadere-confirm").start()


def confirma_si_emite(cam_id, frame, bbox, ev, emit_fn):
    """Confirmă căderea cu VLM (dacă `cadere_vlm`), apoi emite evenimentul `cadere`. Sincron —
    apelat într-un fir din pas_pe_cadru. La eșec VLM lasă să treacă (nu pierdem o cădere reală)."""
    if settings.get("cadere_vlm"):
        try:
            if not _vlm_confirm(frame, bbox):
                logare.info(f"[cadere] cam{cam_id}: VLM a infirmat, nu escaladez")
                return False
        except Exception as e:
            logare.info(f"[cadere] cam{cam_id}: VLM eșuat, las să treacă: {str(e)[:60]}")
    try:
        emit_fn("cadere", cam_id, {"scor": ev.get("scor")})
    except Exception as e:
        logare.info(f"[cadere] cam{cam_id}: emit eșuat: {str(e)[:60]}")
        return False
    logare.info(f"[cadere] cam{cam_id}: CĂDERE confirmată, emis")
    return True


def _vlm_confirm(frame_bgr, bbox):
    """Întrebare da/nu la VLM (Qwen) pe decupajul persoanei: e căzută pe jos?"""
    import cv2
    import describe
    from PIL import Image
    h, w = frame_bgr.shape[:2]
    x1, y1, x2, y2 = (int(v) for v in bbox)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return False
    pil = Image.fromarray(cv2.cvtColor(frame_bgr[y1:y2, x1:x2], cv2.COLOR_BGR2RGB))
    ans = (describe.describe_crop(pil, PROMPT_CADERE) or "").strip().lower()
    return _raspuns_cadere(ans)


_NEG_PREFIX = re.compile(r"^\s*(nu|no|nope|negativ|incert|neclar|nesigur)\b")
_POZ_PREFIX = re.compile(r"^\s*(da|yes|yep|afirmativ)\b")
_POZ_CUV = ("căzut", "cazut", "întins", "intins", "prăbuș", "prabus",
            "fallen", "lying", "collapsed", "pe jos", "on the ground", "la sol")
# Negatii: contractii engleze (`n't`: hasn't/isn't/doesn't/can't/won't…), forme scrise
# (`not `, `has not`, `have not` contin toate „not "), plus negatii romanesti si posturi care
# nu-s cadere. „n't" prinde orice contractie fara o lista exhaustiva.
_NEG_CUV = ("nu ", "n-", "n't", "not ", " not", "no ", "never", "cannot", "fără",
            "niciun", "nicio", "în picioare", "in picioare", "standing", "sitting",
            "seated", "așezat", "asezat", "ghemuit", "aplecat")


def _raspuns_cadere(ans):
    """Verdict pe raspunsul VLM (VLM-ul e instruit sa raspunda da/nu). Prefixul da/nu decide
    cazul comun; pentru raspunsuri verbose, un cuvant pozitiv conteaza DOAR daca nu apare nicio
    negatie — ca «the person hasn't fallen» sau «nu, persoana nu este cazuta» sa NU iasa pozitiv
    doar fiindca apare «fallen»/«cazut» (audit #8). Negatia explicita castiga mereu; raspuns gol
    sau neconcludent -> False (nu escaladam pe nesiguranta)."""
    if not ans:
        return False
    if _NEG_PREFIX.search(ans):
        return False
    if _POZ_PREFIX.search(ans):
        return True
    neg = any(n in ans for n in _NEG_CUV)
    poz = any(p in ans for p in _POZ_CUV)
    return poz and not neg
