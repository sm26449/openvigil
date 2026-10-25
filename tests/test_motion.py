"""Teste pentru motion-gating din app/detect.py (logica pura, fara model/camera)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import detect  # noqa: E402


def _gri(val, shape=(90, 160)):
    return np.full(shape, val, dtype=np.uint8)


def test_fara_referinta_e_miscare():
    # prima data (fara cadru anterior) tratam ca miscare, ca sa nu ratam nimic
    assert detect.has_motion(None, _gri(100))
    # dimensiuni schimbate -> tot miscare (nu putem compara)
    assert detect.has_motion(_gri(100, (90, 160)), _gri(100, (45, 80)))


def test_scena_identica_nu_e_miscare():
    a = _gri(120)
    assert not detect.has_motion(a, a.copy())


def test_zgomot_fin_sub_prag_nu_e_miscare():
    prev = _gri(120)
    cur = prev.copy()
    # cateva pixeli cu diferenta mica (sub pix_delta) -> nu conteaza
    cur[0, 0] = 130   # delta 10 < 18
    assert not detect.has_motion(prev, cur)


def test_obiect_care_apare_e_miscare():
    prev = _gri(120)
    cur = prev.copy()
    # un bloc de pixeli clar diferiti (o persoana care intra) peste pragul de fractie
    cur[20:60, 40:100] = 220
    assert detect.has_motion(prev, cur)


def test_pragul_de_fractie_conteaza():
    prev = _gri(100, (100, 100))   # 10000 px
    cur = prev.copy()
    cur[:2, :10] = 250             # 20 px schimbati = 0.2% < 0.3% prag
    assert not detect.has_motion(prev, cur)
    cur[:5, :10] = 250             # 50 px = 0.5% > prag
    assert detect.has_motion(prev, cur)


def test_gate_ratio_gol():
    detect._gate_stats.update(total=0, gated=0)
    assert detect.gate_ratio() == 0.0
    detect._gate_stats.update(total=100, gated=73)
    assert detect.gate_ratio() == 0.73


def test_motion_region_gaseste_zona():
    prev = _gri(100, (100, 100))
    cur = prev.copy()
    cur[70:90, 60:80] = 220        # bloc de miscare in coltul dreapta-jos
    r = detect.motion_region(prev, cur)
    assert r is not None
    x1, y1, x2, y2 = r
    assert 0.55 < x1 < 0.65 and 0.65 < y1 < 0.75   # incepe pe la 60%,70%
    assert 0.75 < x2 <= 0.82 and 0.85 < y2 <= 0.92
    # scena identica -> fara regiune
    assert detect.motion_region(prev, prev.copy()) is None
    assert detect.motion_region(None, cur) is None


def test_expand_region_cu_clamp():
    import pytest
    # regiune interioara: se mareste cu marginea
    x1, y1, x2, y2 = detect.expand_region((0.4, 0.4, 0.6, 0.6), margin=0.5)
    assert x1 == pytest.approx(0.3) and x2 == pytest.approx(0.7)
    # regiune lipita de margine: clamp la [0,1], nu iese
    x1, y1, x2, y2 = detect.expand_region((0.0, 0.0, 0.2, 0.2), margin=0.5)
    assert x1 == 0.0 and y1 == 0.0 and x2 == pytest.approx(0.3)
