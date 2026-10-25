"""Teste pentru app/ai_analytics.py — geometria de linii/zone/viteza (pura)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ai_analytics  # noqa: E402


@pytest.fixture(autouse=True)
def _cfg(monkeypatch):
    cfg = {
        "count_lines": {"14": [{"name": "poarta", "p1": [0.0, 0.5], "p2": [1.0, 0.5]}]},
        "count_zones": {"14": [{"name": "curte", "poly": [[0.2, 0.2], [0.8, 0.2],
                                                          [0.8, 0.8], [0.2, 0.8]]}]},
        "speed_cal": {"14": 0.05},
    }
    monkeypatch.setattr(ai_analytics.settings, "get", lambda k: cfg.get(k))


WH = (640, 480)   # linia orizontala e la y=240


def test_trecere_de_sus_in_jos():
    # obiect care coboara peste linia y=0.5 -> o traverseaza
    cr = ai_analytics.crossings("14", (320, 100), (320, 300), WH)
    assert len(cr) == 1 and cr[0][0] == "poarta"


def test_fara_trecere_daca_nu_atinge_linia():
    cr = ai_analytics.crossings("14", (320, 100), (320, 200), WH)   # ramane deasupra
    assert cr == []


def test_sens_opus_la_traversare_inversa():
    jos = ai_analytics.crossings("14", (320, 100), (320, 300), WH)[0][1]
    sus = ai_analytics.crossings("14", (320, 300), (320, 100), WH)[0][1]
    assert jos != sus   # intra vs iese


def test_camera_fara_linii_nu_da_treceri():
    assert ai_analytics.crossings("99", (0, 0), (640, 480), WH) == []


def test_intrare_in_zona():
    # din afara (10,10) in interiorul poligonului (320,240)
    z = ai_analytics.zone_entries("14", (10, 10), (320, 240), WH)
    assert z == ["curte"]


def test_fara_intrare_daca_era_deja_inauntru():
    z = ai_analytics.zone_entries("14", (300, 240), (320, 240), WH)
    assert z == []


def test_viteza_px_s():
    t = {"x0": 0, "y0": 0, "x": 100, "y": 0, "t0": 0.0, "last": 2.0}
    assert ai_analytics.speed_px_s(t) == 50.0   # 100px / 2s


def test_kmh_cu_calibrare():
    # 50 px/s * 0.05 m/px * 3.6 = 9 km/h
    assert ai_analytics.kmh("14", 50.0) == 9.0


def test_kmh_none_fara_calibrare():
    assert ai_analytics.kmh("99", 50.0) is None
