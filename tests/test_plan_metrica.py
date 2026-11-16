"""Masura planului (app/plan_metrica.py): scara din limita proprietatii, deformarea, unghiurile."""
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import plan_metrica as pm  # noqa: E402

W, H = 1000, 500


def _lim(pts_m, w_px=None, deformare=(1.0, 1.0), laturi=None):
    """Un poligon in METRI (teren) desenat pe un plan cu 10 px/m, eventual intins pe x/y."""
    sx, sy = 10 * deformare[0], 10 * deformare[1]
    puncte = [[(100 + x * sx) / W, (50 + y * sy) / H] for x, y in pts_m]
    n = len(pts_m)
    lung = {str(i): round(math.dist(pts_m[i], pts_m[(i + 1) % n]), 3) for i in range(n)}
    return {"puncte": puncte, "laturi": {k: v for k, v in lung.items() if laturi is None or int(k) in laturi}}


DREPTUNGHI = [(0, 0), (60, 0), (60, 35), (0, 35)]


def test_izotrop_din_o_latura():
    m = pm.calculeaza(W, H, _lim(DREPTUNGHI, laturi=[0]))
    assert m["mod"] == "izotrop" and m["sursa"] == "limita"
    assert [x["m_plan"] for x in m["laturi"]] == [60, 35, 60, 35]
    assert m["unghiuri"] == [90, 90, 90, 90]
    assert m["suprafata"] == pytest.approx(2100, rel=1e-3) and m["perimetru"] == pytest.approx(190, rel=1e-3)
    assert not m["avertismente"]


def test_forma_l_are_un_colt_de_270():
    l = [(0, 0), (40, 0), (40, 20), (20, 20), (20, 30), (0, 30)]
    m = pm.calculeaza(W, H, _lim(l, laturi=[0]))
    assert sorted(m["unghiuri"]) == [90, 90, 90, 90, 90, 270]
    assert m["suprafata"] == pytest.approx(40 * 20 + 20 * 10, rel=1e-3)
    # aceeasi forma parcursa invers: aceleasi unghiuri interioare
    m2 = pm.calculeaza(W, H, _lim(l[::-1], laturi=[0]))
    assert sorted(m2["unghiuri"]) == sorted(m["unghiuri"])


def test_schita_intinsa_se_corecteaza_cu_3_laturi():
    # schita intinsa cu 40% pe orizontala: dreptunghiul de 60x35 m pare 84x35 pe plan
    pts = [(0, 0), (60, 0), (60, 35), (0, 35), (-10, 15)]   # 5 colturi, ca directiile sa difere
    m = pm.calculeaza(W, H, _lim(pts, deformare=(1.4, 1.0)))
    assert m["mod"] == "anizotrop" and m["deformare"]["raport"] == pytest.approx(1.4, rel=1e-2)
    assert all(abs(x["abatere"]) < 1e-3 for x in m["laturi"])
    assert m["unghiuri"][1] == pytest.approx(90, abs=0.2)          # unghiurile reale, nu cele din schita
    assert m["suprafata"] == pytest.approx(60 * 35 + 35 * 10 / 2, rel=1e-3)


def test_zgomotul_de_masurare_nu_e_luat_drept_deformare():
    lim = _lim(DREPTUNGHI)
    lim["laturi"] = {"0": 61.2, "1": 34.4, "2": 59.1, "3": 35.6}     # ±2% — masuratori aproximative
    m = pm.calculeaza(W, H, lim)
    assert m["mod"] == "izotrop" and not m["avertismente"]
    assert m["abatere_max"] < 0.03


def test_o_lungime_gresita_da_avertisment():
    lim = _lim(DREPTUNGHI, laturi=[0, 1])
    lim["laturi"]["1"] = 70.0                                        # 35 m scris 70
    m = pm.calculeaza(W, H, lim)
    assert m["mod"] == "izotrop" and m["avertismente"]
    assert m["abatere_max"] > 0.1


def test_scara_din_doua_clicuri_si_fara_nimic():
    m = pm.calculeaza(W, H, scara={"x1": 0.1, "y1": 0.5, "x2": 0.6, "y2": 0.5, "m": 25})
    assert m["sursa"] == "scara" and m["mod"] == "izotrop"
    assert math.sqrt(m["G"][0]) == pytest.approx(25 / 500)          # 0,05 m pe pixel
    assert pm.calculeaza(W, H) is None
    assert pm.calculeaza(W, H, {"puncte": _lim(DREPTUNGHI)["puncte"], "laturi": {}}) is None
    # limita cu laturi cunoscute are prioritate fata de scara
    m = pm.calculeaza(W, H, _lim(DREPTUNGHI, laturi=[0]), {"x1": 0, "y1": 0, "x2": 1, "y2": 0, "m": 1})
    assert m["sursa"] == "limita"


def test_A_duce_pixelii_in_metri():
    pts = [(0, 0), (60, 0), (60, 35), (0, 35), (-10, 15)]
    m = pm.calculeaza(W, H, _lim(pts, deformare=(1.4, 1.0)))
    a, ainv, g = np.array(m["A"]), np.array(m["Ainv"]), np.array([[m["G"][0], m["G"][1]], [m["G"][1], m["G"][2]]])
    assert np.allclose(a.T @ a, g) and np.allclose(a @ ainv, np.eye(2))


@pytest.mark.parametrize("rau", [
    {"puncte": [[0.1, 0.1], [0.2, 0.2]]},                                           # prea putine
    {"puncte": [[0.1, 0.1], [0.9, 0.1], [0.1, 0.9], [0.9, 0.9]]},                   # se intersecteaza (fluture)
    {"puncte": [[0.1, 0.1], [0.9, 0.1], [1.2, 0.9]]},                               # in afara planului
    {"puncte": [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]], "laturi": {"5": 10}},          # latura inexistenta
    {"puncte": [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]], "laturi": {"0": 0}},           # lungime nula
    {"puncte": [[0.1, 0.1], [0.1, 0.1], [0.5, 0.9]]},                               # colturi suprapuse
    {"puncte": "nu"},
])
def test_validare(rau):
    with pytest.raises(ValueError):
        pm.valideaza_limita(rau)


def test_validare_normalizeaza():
    v = pm.valideaza_limita({"puncte": [[0.1, 0.1], [0.9, 0.1], [0.5, 0.9]], "laturi": {"0": "70", "1": "", 2: None}})
    assert v["laturi"] == {"0": 70.0} and len(v["puncte"]) == 3
