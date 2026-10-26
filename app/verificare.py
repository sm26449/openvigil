#!/usr/bin/env python3
"""Verificarea publica a probelor: un fisier exportat (sau un hash) -> „e al nostru si neatins?”.

Registrul exporturilor (data/exporturi.jsonl) retine, la fiecare export si fisa probatorie,
amprenta SHA-256 a fisierului livrat, camera, intervalul, cine l-a cerut si cand. Pagina
publica /verifica (fara login) calculeaza hash-ul fisierului urcat si il cauta: in registrul
exporturilor si in lantul de custodie al segmentelor brute (data/segmente.db). Raspunsul spune
doar ce e nevoie pentru o verificare: ca fisierul a fost produs de acest NVR, cand, de pe ce
camera si ca n-a fost modificat de atunci. Nimic altceva nu se dezvaluie.
"""
import hashlib
import json
import os
import re
import threading
import time
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FISIER = os.path.join(ROOT, "data", "exporturi.jsonl")
_lock = threading.Lock()
_SHA = re.compile(r"^[0-9a-f]{64}$")


def inregistreaza(sha256, octeti, cam, start=None, end=None, fel="export", cine=None, nume=None):
    """Un fisier livrat intra in registru (apelat din jobul de export si din fisa probatorie)."""
    rec = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "sha256": sha256, "octeti": int(octeti),
           "cam": int(cam), "start": start, "end": end, "fel": fel, "cine": cine, "nume": nume}
    with _lock:
        os.makedirs(os.path.dirname(FISIER), exist_ok=True)
        with open(FISIER, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    return rec


def _exporturi():
    try:
        with open(FISIER, encoding="utf-8") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def hash_flux(stream, limita=2 * 1024 ** 3):
    """SHA-256 pe un flux de octeti, in bucati; None daca depaseste limita."""
    h = hashlib.sha256()
    n = 0
    while True:
        b = stream.read(1 << 20)
        if not b:
            break
        n += len(b)
        if n > limita:
            return None, n
        h.update(b)
    return h.hexdigest(), n


def cauta(sha256, nume_camere=None):
    """Ce stim despre amprenta asta: {gasit, fel, cam, cam_nume, start, end, t, cine, nume} sau
    {gasit: False}. Intai registrul exporturilor (cel mai nou), apoi segmentele brute."""
    sha256 = (sha256 or "").strip().lower()
    if not _SHA.match(sha256):
        return {"gasit": False, "motiv": t("hash invalid")}
    nume_camere = nume_camere or {}
    potriviri = [r for r in _exporturi() if r.get("sha256") == sha256]
    if potriviri:
        r = potriviri[-1]
        return {"gasit": True, "fel": r.get("fel", "export"), "cam": r["cam"],
                "cam_nume": nume_camere.get(r["cam"], f"cam {r['cam']}"), "start": r.get("start"),
                "end": r.get("end"), "t": r["t"], "cine": r.get("cine"), "nume": r.get("nume"),
                "octeti": r.get("octeti")}
    try:
        import lant
        s = lant.cauta_hash(sha256)
    except Exception:
        s = None
    if s:
        cand = s.get("calculat") or s.get("cand")
        return {"gasit": True, "fel": "segment", "cam": s["cam"],
                "cam_nume": nume_camere.get(s["cam"], f"cam {s['cam']}"), "nume": s["nume"],
                "t": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(cand)) if cand else None,
                "octeti": s.get("octeti"), "start": None, "end": None, "cine": None}
    return {"gasit": False}
