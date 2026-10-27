"""Motorul de detectie pe schimbare (app/scena.py), cu cadre sintetice: fundal static,
un obiect care trece -> candidat matur; zgomot care tremura pe loc -> nu; schimbare globala
de lumina -> reset, nu candidat; decupajul cu margine."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
cv2 = pytest.importorskip("cv2")
import scena  # noqa: E402

W, H = 640, 360


def _fundal(seed=1):
    rng = np.random.default_rng(seed)
    img = rng.integers(60, 120, (H, W, 3), dtype=np.uint8)      # textura statica
    return img


def _cu_obiect(f, x, y, w=60, h=100, val=230):
    g = f.copy()
    g[y:y + h, x:x + w] = val
    return g


def _antreneaza(s, f, n=60, t0=0.0):
    t = t0
    for _ in range(n):
        s.feed(f, now=t)
        t += 0.25
    return t


def test_obiect_care_trece_devine_candidat_matur():
    f = _fundal()
    s = scena.Scena(1, {"istoric": 200})
    t = _antreneaza(s, f)
    maturi = []
    for i in range(12):                     # un dreptunghi care merge spre dreapta
        maturi += s.feed(_cu_obiect(f, 100 + i * 12, 120), now=t)
        t += 0.25
    assert maturi, "obiectul in miscare trebuie sa ajunga candidat matur"
    c = maturi[0]
    nb = c.nbox(s.w, s.h)
    assert 0.1 < nb[0] < 0.5 and 0.2 < nb[1] < 0.5 and c.cadre >= 3
    assert c.deplasare() >= s.cfg["deplasare_min"] * s.w
    # acelasi candidat nu e cerut din nou la clasificare pana nu trece reclasificare_sec
    c.clasificat = {"eticheta": "om"}
    c.ultima_clasificare = t
    assert c not in s.feed(_cu_obiect(f, 100 + 12 * 12, 120), now=t + 0.25)


def test_tremurul_pe_loc_nu_e_candidat():
    f = _fundal()
    s = scena.Scena(1, {"istoric": 200, "instabil_rata": 0.05})   # harta se invata repede in test
    t = _antreneaza(s, f)
    rng = np.random.default_rng(7)
    maturi = []
    for _ in range(120):                    # un petic care clipeste pe loc (frunze in vant)
        g = f.copy()
        if rng.random() < 0.7:
            dx, dy = rng.integers(-4, 5), rng.integers(-4, 5)
            g[200 + dy:240 + dy, 300 + dx:340 + dx] = rng.integers(130, 255)
        maturi += s.feed(g, now=t)
        t += 0.25
    assert not maturi
    assert s.stare()["instabil_pct"] > 0   # zona a fost marcata instabila


def test_schimbarea_globala_de_lumina_reseteaza():
    f = _fundal()
    s = scena.Scena(1, {"istoric": 200})
    t = _antreneaza(s, f)
    luminos = np.clip(f.astype(np.int16) + 110, 0, 255).astype(np.uint8)
    assert s.feed(luminos, now=t) == []
    assert s.resetari == 1 and s.candidati == {}
    assert s.stare()["schimbat_pct"] > 40


def test_fundalul_si_decupajul():
    f = _fundal()
    s = scena.Scena(1, {"istoric": 200})
    _antreneaza(s, f, n=30)
    bg = s.fundal()
    assert bg is not None and bg.shape[1] == scena.W
    crop = scena.decupaj(f, (0.5, 0.5, 0.05, 0.1))
    assert crop is not None and crop.shape[1] >= 96 and crop.shape[0] >= 96
    assert scena.decupaj(f, (1.2, 1.2, 0.01, 0.01)) is None or True   # in afara: nu arunca


def test_uneste_cutii():
    assert scena._uneste([(0, 0, 10, 10), (5, 5, 10, 10), (50, 50, 5, 5)]) == [(0, 0, 15, 15), (50, 50, 5, 5)]
