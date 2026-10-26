#!/usr/bin/env python3
"""Re-encodare inregistrari vechi pentru a castiga spatiu (optional, din Setari).

Cum functioneaza: segmentele mai vechi de X ore se recomprima cu un codec mai eficient
(HEVC sau AV1) si, daca fisierul nou e semnificativ mai mic, copia recomprimata MERGE PE
NIVELUL DE ARHIVA (`arhiva_dir`), iar originalul se sterge de pe discul principal.
Originalele nu se mai rescriu in loc (auditul: o recodare cu pierderi peste material de
proba, si liste concat cu codecuri amestecate); fara arhiva configurata functia nu ruleaza.
Rularea e serializata (un fisier pe rand) si se opreste automat cand incarcarea e mare.

IMPORTANT: are sens doar cu encoder HARDWARE — verificarea de mai jos detecteaza lipsa
lui si atunci functia ramane indisponibila. Pe GPU-ul actual (RTX 2000 Ada) exista
NVENC HEVC si AV1; AV1 scade cu ~30-40% fata de HEVC.
"""
import os
import subprocess
import threading
import time

import record
import settings
import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIN_AGE_H = 6        # nu atingem inregistrarile recente
MIN_GAIN = 0.15      # pastram noul fisier doar daca scade cu >=15%
MAX_LOAD = 0.6       # peste 60% incarcare medie pe core, asteptam

state = {"running": False, "done": 0, "saved_mb": 0, "last": "", "encoder": None}


def available_encoder():
    """Cel mai bun encoder hardware disponibil: av1_nvenc > hevc_nvenc > None (doar CPU)."""
    if state["encoder"] is not None:
        return state["encoder"] or None
    found = ""
    for enc in ("av1_nvenc", "hevc_nvenc"):
        try:
            subprocess.run(["ffmpeg", "-hide_banner", "-f", "lavfi",
                            "-i", "testsrc=size=320x240:rate=10", "-frames:v", "5",
                            "-c:v", enc, "-f", "null", "-"],
                           capture_output=True, timeout=30, check=True)
            found = enc
            break
        except Exception:
            continue
    state["encoder"] = found
    return found or None


def _candidates():
    """Fisierele destul de vechi, care nu au fost deja re-encodate (marcate cu .re.mp4)."""
    out = []
    cutoff = time.time() - MIN_AGE_H * 3600
    try:
        for name in os.listdir(record.REC_DIR):
            if not name.startswith("cam"):
                continue
            for t, p, fn in record._files(int(name[3:]), arhiva=False):
                if t < cutoff and not fn.endswith(".re.mp4"):
                    out.append(p)
    except (FileNotFoundError, ValueError):
        pass
    return sorted(out)


def _reencode_one(path, enc):
    """Recomprima un fisier; il inlocuieste doar daca scade semnificativ. Bytes economisiti."""
    tmp = path[:-4] + ".re.mp4"
    before = os.path.getsize(path)
    # enc e mereu NVENC aici (altfel _worker nu porneste), deci si decodarea merge pe GPU
    cmd = ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-hwaccel", "cuda", "-i", path,
           "-c:v", enc, "-preset", "p5", "-rc", "vbr", "-cq", "30", "-b:v", "0",
           "-c:a", "copy", "-movflags", "+faststart", tmp]
    if enc == "hevc_nvenc":
        cmd += ["-tag:v", "hvc1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=900)
        if r.returncode != 0 or not os.path.exists(tmp):
            raise RuntimeError(r.stderr.decode(errors="ignore")[-160:])
        after = os.path.getsize(tmp)
        if after < before * (1 - MIN_GAIN) and after > 10000:
            # copia recomprimata merge in nivelul de arhiva (acelasi nume: redarea o vede
            # prin record._files), originalul pleaca de pe discul principal
            dest_dir = os.path.join(record.archive_dir(), os.path.basename(os.path.dirname(path)))
            os.makedirs(dest_dir, exist_ok=True)
            dest = os.path.join(dest_dir, os.path.basename(path))
            import shutil
            shutil.move(tmp, dest)
            os.remove(path)
            return before - after
        os.remove(tmp)
        return 0
    except Exception:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _worker():
    enc = available_encoder()
    if not enc:
        state.update(running=False, last=t("niciun encoder hardware disponibil"))
        return
    if not record.archive_dir() or not os.path.isdir(record.archive_dir()):
        state.update(running=False, last=t("recodarea cere un nivel de arhiva configurat (Setari -> Inregistrare)"))
        return
    for path in _candidates():
        if not settings.get("reencode_enabled"):
            break
        # nu concuram cu inregistrarea live
        while os.getloadavg()[0] / (os.cpu_count() or 1) > MAX_LOAD:
            time.sleep(30)
            if not settings.get("reencode_enabled"):
                break
        try:
            saved = _reencode_one(path, enc)
            state["done"] += 1
            state["saved_mb"] += saved // 2**20
            state["last"] = f"{os.path.basename(path)}: -{saved // 2**20} MB"
        except Exception as e:
            state["last"] = f"{os.path.basename(path)}: {e}"
    state["running"] = False


def tick():
    """Porneste o runda daca optiunea e activa; se reprogrameaza orar."""
    try:
        if settings.get("reencode_enabled") and not state["running"] and available_encoder():
            state["running"] = True
            threading.Thread(target=_worker, daemon=True).start()
    except Exception as e:
        logare.error(f"[reencode] pornirea rundei a esuat: {str(e)[:80]}")
    t = threading.Timer(3600, tick)
    t.daemon = True
    t.start()
