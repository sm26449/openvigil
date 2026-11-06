#!/usr/bin/env python3
"""Regula de cădere — orchestrarea (partea nvr-ai). Leagă percepția (pose) de logica pură
(reguli.scor_cadere + dwell), confirmă cu VLM și, la declanșare, emite un eveniment `cadere`.
Politica și escaladarea (incident critic, Telegram, alertd) le face nvr-web pe evenimentul ăsta
— vezi docs/reguli-analiza.md.

Pose și VLM sunt INJECTATE (`keypoints_fn`, `confirm_fn`), ca orchestratorul să fie testabil
fără model. În viață le dau `cadere_pose.keypoints` și o întrebare la Qwen.
"""
import time

import settings
import logare
import reguli

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
