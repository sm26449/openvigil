#!/usr/bin/env python3
"""Joburile de antrenament (ruleaza in nvr-ai): construirea datasetului etichetat si
antrenarea YOLO, ca subprocese in venv-ul proiectului, cu progres citit din stdout.

Un singur job odata (GPU-ul e unul). Starea e un dict simplu, servit de /train/stare:
  {activ, tip, nume, inceput, sfarsit, progres, total, mesaj, log (ultimele linii), rc, rezultat}
Tipuri:
  dataset   -> scripts/finetune_dataset.py [--max N]   (teacher yolo11x + SigLIP pe specii)
  antrenare -> scripts/antreneaza.py --nume curte-vN --epoci E   (ultralytics, pe GPU)
  proba     -> doar pentru teste: un proces care tipareste progres si iese
Liniile de progres au formatul "[dataset] i/N" / "[train] epoca i/N ..." / "[train] gata ...".
"""
import collections
import os
import re
import subprocess
import sys
import threading
import time

import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
FINETUNE = os.path.join(ROOT, "data", "finetune")
_lock = threading.Lock()
_proc = None
stare = {"activ": False, "tip": None, "nume": None, "inceput": None, "sfarsit": None,
         "progres": 0, "total": 0, "mesaj": "", "log": [], "rc": None, "rezultat": None}
_log = collections.deque(maxlen=30)

_RE_DATASET = re.compile(r"^\[dataset\] (\d+)/(\d+)")
_RE_EPOCA = re.compile(r"^\[train\] epoca (\d+)/(\d+)(.*)$")
_RE_GATA = re.compile(r"^\[train\] gata best=(\S+)")


def parseaza(linie, st):
    """Actualizeaza starea dintr-o linie de stdout; True daca linia era de progres."""
    m = _RE_DATASET.match(linie)
    if m:
        st["progres"], st["total"] = int(m[1]), int(m[2])
        st["mesaj"] = t("etichetare {i}/{total}", i=m[1], total=m[2])
        return True
    m = _RE_EPOCA.match(linie)
    if m:
        st["progres"], st["total"] = int(m[1]), int(m[2])
        st["mesaj"] = t("epoca {i}/{total}{rest}", i=m[1], total=m[2], rest=m[3])
        return True
    m = _RE_GATA.match(linie)
    if m:
        st["rezultat"] = m[1]
        st["mesaj"] = t("antrenament încheiat")
        return True
    return False


def nume_urmator():
    """curte-v<N+1> dupa cel mai mare N existent in data/finetune/runs."""
    n = 0
    d = os.path.join(FINETUNE, "runs")
    for x in (os.listdir(d) if os.path.isdir(d) else []):
        m = re.match(r"^curte-v(\d+)$", x)
        if m:
            n = max(n, int(m[1]))
    return f"curte-v{n + 1}"


def _comanda(tip, epoci=None, maxim=None):
    if tip == "dataset":
        cmd = [PY, os.path.join(ROOT, "scripts", "finetune_dataset.py")]
        if maxim:
            cmd += ["--max", str(int(maxim))]
        return cmd, None
    if tip == "antrenare":
        nume = nume_urmator()
        return [PY, os.path.join(ROOT, "scripts", "antreneaza.py"), "--nume", nume,
                "--epoci", str(int(epoci or 30))], nume
    if tip == "proba":
        return [PY, "-u", "-c",
                "import time\nfor i in range(1,4):\n print(f'[train] epoca {i}/3 mAP50=0.5',flush=True); time.sleep(0.2)\n"
                "print('[train] gata best=/tmp/x.pt',flush=True)"], "proba"
    raise ValueError(t("tip de job necunoscut: {tip}", tip=tip))


def porneste(tip, epoci=None, maxim=None):
    """Porneste jobul daca nu ruleaza altul. Intoarce starea (sau ridica RuntimeError)."""
    global _proc
    with _lock:
        if stare["activ"]:
            raise RuntimeError(t("rulează deja un job ({tip})", tip=stare['tip']))
        cmd, nume = _comanda(tip, epoci, maxim)
        _log.clear()
        stare.update(activ=True, tip=tip, nume=nume, inceput=time.time(), sfarsit=None,
                     progres=0, total=0, mesaj=t("pornit"), log=[], rc=None, rezultat=None)
        env = dict(os.environ, PYTHONUNBUFFERED="1")
        try:
            _proc = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, env=env, start_new_session=True)
        except OSError as e:
            stare.update(activ=False, rc=127, sfarsit=time.time(), mesaj=t("nu pot porni: {e}", e=str(e)[:120]))
            raise RuntimeError(t("nu pot porni jobul: {e}", e=str(e)[:120]))
        threading.Thread(target=_urmareste, args=(_proc,), daemon=True, name="antrenare").start()
    logare.info(f"[antrenare] {tip} pornit: {' '.join(cmd[-4:])}")
    return dict(stare)


def _urmareste(p):
    for linie in p.stdout:
        linie = linie.rstrip("\n")
        if not linie:
            continue
        _log.append(linie[:200])
        parseaza(linie, stare)
        stare["log"] = list(_log)
    rc = p.wait()
    with _lock:
        oprit = stare["mesaj"] == t("oprit de utilizator")
        stare.update(activ=False, rc=rc, sfarsit=time.time(), log=list(_log))
        if oprit:
            pass                                   # oprirea ceruta nu e un esec
        elif rc != 0:
            stare["mesaj"] = t("eșuat (cod {rc}): {ultim}", rc=rc, ultim=(_log[-1] if _log else ""))
        elif stare["tip"] == "dataset":
            stare["mesaj"] = t("dataset construit")
    logare.info(f"[antrenare] {stare['tip']} încheiat, cod {rc}")


def opreste():
    global _proc
    with _lock:
        if not stare["activ"] or _proc is None:
            return False
        try:
            os.killpg(os.getpgid(_proc.pid), 15)
        except OSError:
            return False
        stare["mesaj"] = t("oprit de utilizator")
        return True


def curent():
    s = dict(stare)
    s["log"] = list(_log)[-8:]
    return s
