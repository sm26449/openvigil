#!/usr/bin/env python3
"""Metrici per model (faza 6): cat de des si cat de repede lucreaza fiecare.

"GPU-ul e la 73%" nu spune CINE munceste; "face: 4/min la 380ms, semantic are
120 in coada" spune. Un cronometru ieftin in jurul fiecarei inferente:

    with metrici.cronometru("face"):
        ...inferenta...

Pe nume se tin: apelurile totale, latenta medie/ultima si un inel de timpi
recenti din care iese rata pe minut. Pur, thread-safe, zero dependinte —
snapshot() e citit de /metrici (nvr-ai) si ajunge in /api/system + pagina Stare.
"""
import collections
import threading
import time
from contextlib import contextmanager

FEREASTRA = 300          # secunde: rata "pe minut" se calculeaza din acest inel

_date = {}               # nume -> {n, total_s, ultim_s, recente: deque[timestamps]}
_lock = threading.Lock()


def record(nume, durata_s, now=None):
    """Consemneaza o inferenta incheiata (durata in secunde)."""
    now = now or time.time()
    with _lock:
        d = _date.get(nume)
        if d is None:
            d = _date[nume] = {"n": 0, "total_s": 0.0, "ultim_s": 0.0,
                               "recente": collections.deque(maxlen=1000)}
        d["n"] += 1
        d["total_s"] += durata_s
        d["ultim_s"] = durata_s
        d["recente"].append(now)


@contextmanager
def cronometru(nume):
    t0 = time.perf_counter()
    try:
        yield
    finally:
        record(nume, time.perf_counter() - t0)


def snapshot(now=None):
    """{nume: {apeluri, pe_min, ms_mediu, ms_ultim}} — pentru /metrici si Stare."""
    now = now or time.time()
    out = {}
    with _lock:
        for nume, d in _date.items():
            recente = sum(1 for t in d["recente"] if now - t <= FEREASTRA)
            out[nume] = {
                "apeluri": d["n"],
                "pe_min": round(recente * 60.0 / FEREASTRA, 2),
                "ms_mediu": round(d["total_s"] / d["n"] * 1000) if d["n"] else 0,
                "ms_ultim": round(d["ultim_s"] * 1000),
            }
    return out
