#!/usr/bin/env python3
"""Serviciul de inregistrare (nvr-record), separat de interfata web: restarturile
nvr-web (setari, modele noi) nu mai intrerup inregistrarea continua.

Urmareste data/cameras.json (mtime) si porneste/opreste recorderele; retentia ruleaza
orar (record.prune). Logica efectiva e in record.py — aici e doar bucla de supraveghere.
"""
import json
import threading
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import continuity
import escaladare
import lant
import notify
import record
import sdnotify
import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_FILE = os.path.join(ROOT, "data", "cameras.json")


def load_all():
    """Camerele de inregistrat: registrul unic, minus cele cu inregistrarea oprita.
    Toate sunt accesate DIRECT — daca NVR-ul pica, inregistrarea continua."""
    try:
        with open(EXT_FILE) as f:
            cams = json.load(f)
    except (FileNotFoundError, ValueError):
        return []
    return [c for c in cams if c.get("record", "continuous") != "off"]


def all_cams():
    try:
        with open(EXT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def motion_cleanup():
    """Pentru camerele pe modul 'la miscare': sterge segmentele fara evenimente.
    Ruleaza la 10 minute (segmentele sunt de 5 min, deci e destul de des)."""
    # Probele („pastreaza ca proba") nu se sterg: le citim o data pentru toate camerele.
    # Daca baza nu raspunde, saram cleanup-ul (ca la retentie) — nu riscam o proba.
    try:
        import probe
        prot = probe.active_ranges()
    except Exception as e:
        logare.info(f"[record] motion cleanup amânat — nu pot citi probele: {e}")
        prot = None
    if prot is None:
        t = threading.Timer(600, motion_cleanup)
        t.daemon = True
        t.start()
        return
    for cam in all_cams():
        if cam.get("record") != "motion":
            continue
        try:
            n = record.prune_motion(cam["id"], record.event_times(cam["id"]),
                                    protejat=prot.get(cam["id"]))
            if n:
                logare.info(f"[record] cam{cam['id']}: sterse {n} segmente fara miscare")
        except Exception as e:
            logare.info(f"[record] cleanup cam{cam['id']}: {e}")
    t = threading.Timer(600, motion_cleanup)
    t.daemon = True
    t.start()


def _problema(p):
    """O problema de continuitate NOUA (continuity o anunta o singura data): jurnal + alerta.
    „Nu mai scrie” e un episod: se inchide cand camera scrie din nou (_rezolvat)."""
    logare.info(f"[continuitate] {p['text']}")
    if p.get("grav"):
        mut = p["cheie"].startswith("mut-")
        escaladare.alerta("nvr_inregistrare_problema", "⚠️ " + p["text"], key=p["cheie"],
                          episod=p["cheie"] if mut else None,
                          revenire="nvr_inregistrare_reluata" if mut else None,
                          cam=f"cam{p['cam_id']}" if p.get("cam_id") else None)
    try:
        import hooks
        hooks.emit("gol_inregistrare", p.get("cam_id"), text=p["text"], cheie=p["cheie"])
    except Exception:
        pass


def _rezolvat(p):
    """O problema anuntata a disparut. Doar „nu mai scrie” merita un mesaj (golurile se inchid
    singure la miezul noptii, cand ziua se schimba — acolo nu e nicio vestire)."""
    logare.info(f"[continuitate] rezolvat: {p.get('text')}")
    if p["cheie"].startswith("mut-"):
        nume = (p.get("text") or "").split(":")[0] or f"cam{p.get('cam_id')}"
        escaladare.alerta("nvr_inregistrare_problema",
                          t("✅ {cam}: înregistrarea scrie din nou", cam=nume),
                          episod=p["cheie"], rezolvat=True, cam=f"cam{p.get('cam_id')}")


def asteapta_cheia():
    """In directorul de inregistrari nu se poate scrie (volum nemontat sau read-only, permisiuni,
    director criptat pe gazda fara cheie). Nu crapam: anuntam (jurnal + notificare), spunem READY
    si asteptam pana devine scriibil."""
    if not record.blocat():
        # episodul „inregistrare oprita” ramas deschis de dinainte de o repornire se inchide
        # (nimic deschis = no-op la serviciu; fara rezerva pe bot)
        escaladare.alerta("nvr_inregistrare_oprita", t("✅ Înregistrarea a fost reluată: directorul de înregistrări se poate scrie din nou."),
                          episod="rec-blocat", rezolvat=True, rezerva=False)
        return False
    logare.warning("[record] in directorul de inregistrari nu se poate scrie — astept sa devina scriibil")
    escaladare.alerta("nvr_inregistrare_oprita",
                      t("🔒 Înregistrarea așteaptă: în directorul de înregistrări nu se poate scrie ({dir}): verifică volumul, permisiunile sau cheia de criptare.",
                        dir=record.REC_DIR),
                      key="rec_blocat", episod="rec-blocat", revenire="nvr_inregistrare_reluata")
    sdnotify.ready()
    while record.blocat():
        sdnotify.watchdog()
        time.sleep(10)
    logare.info("[record] directorul e scriibil, pornesc inregistrarea")
    escaladare.alerta("nvr_inregistrare_oprita", t("✅ Înregistrarea a fost reluată: directorul de înregistrări se poate scrie din nou."),
                      episod="rec-blocat", rezolvat=True)
    return True


def main():
    logare.info("[record] pornit")
    gata = asteapta_cheia()
    record.prune()
    motion_cleanup()
    continuity.start(_problema, on_rezolvat=_rezolvat)
    # lantul de custodie: SHA-256 pe fiecare segment inchis (PostgreSQL, tabelul `segmente`, lant.py)
    lant.start(lambda cam: [(t, p, fn) for t, p, fn in record._files(cam)],
               lambda: [c["id"] for c in all_cams()], record.SEGMENT)
    last = None
    morti = 0        # bucle la rand cu un recorder mort: dupa cateva, iesim ca sa fim reporniti
    while True:
        cur = os.path.getmtime(EXT_FILE) if os.path.exists(EXT_FILE) else None
        if cur != last:
            last = cur
            cams = load_all()
            record.sync(cams)
            logare.info(f"[record] sincronizat: {sorted(c['id'] for c in cams)}")
        if not gata:
            sdnotify.ready()          # systemd (Type=notify): serviciul chiar supravegheaza
            gata = True
        # bataia de inima: cat timp bucla asta se invarte si recorderele pornite traiesc
        if all(r.is_alive() for r in record.recorders.values()):
            sdnotify.watchdog()
            morti = 0
        else:
            morti += 1
            if morti >= 6 and not os.environ.get("NOTIFY_SOCKET"):
                logare.error("[record] un recorder e mort de un minut; ies ca sa fiu repornit")
                os._exit(1)
        time.sleep(10)


if __name__ == "__main__":
    main()
