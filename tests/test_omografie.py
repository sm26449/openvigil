"""Omografia cadru -> plan (app/omografie.py) si legarea ei de harta (app/harta.py)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import harta  # noqa: E402
import omografie  # noqa: E402

# o omografie cunoscuta (perspectiva reala: w variaza), din care generam perechi exacte
H = np.array([[0.6, 0.1, 0.1], [0.05, 0.9, -0.2], [0.1, 0.6, 1.0]])


def _pe_plan(x, y):
    v = H @ [x, y, 1]
    return v[0] / v[2], v[1] / v[2]


def _perechi(pts):
    return [[x, y, *_pe_plan(x, y)] for x, y in pts]


COLTURI = [(0.1, 0.5), (0.9, 0.5), (0.95, 0.95), (0.05, 0.95)]


def test_recupereaza_omografia_din_4_si_din_mai_multe_puncte():
    for pts in (COLTURI, COLTURI + [(0.5, 0.7), (0.3, 0.6), (0.7, 0.85)]):
        cal = omografie.calculeaza(_perechi(pts))
        assert cal["eroare"] < 1e-3
        for x, y in [(0.5, 0.8), (0.2, 0.6), (0.8, 0.9)]:
            px, py = omografie.proiecteaza(cal["h"], x, y)
            ex, ey = _pe_plan(x, y)
            assert abs(px - ex) < 1e-3 and abs(py - ey) < 1e-3


def test_eroarea_arata_o_pereche_gresita():
    pp = _perechi(COLTURI + [(0.5, 0.7)])
    pp[4][2] += 0.1                       # al cincilea punct pus gresit pe plan
    assert omografie.calculeaza(pp)["eroare"] > 0.02


@pytest.mark.parametrize("rau", [
    [[0.1, 0.1, 0.1, 0.1]] * 3,                                         # prea putine
    [[0.1 * i, 0.5, 0.1 * i, 0.5] for i in range(1, 5)],                # coliniare
    [[0.1, 0.5, 0.1, 0.5], [0.5, 0.5, 0.5, 0.5], [0.9, 0.5, 0.9, 0.5], [0.5, 0.9, 0.5, 0.9]],   # 3 pe o linie
    _perechi(COLTURI[:3]) + [[1.5, 0.5, 0.5, 0.5]],                     # in afara cadrului
    [[0.1, 0.5, 0.2, "x"]] * 4,                                         # nenumeric
    [[0.1, 0.5]] * 4,                                                   # forma gresita
])
def test_date_proaste(rau):
    with pytest.raises(ValueError):
        omografie.calculeaza(rau)


def test_orizontul_nu_se_proiecteaza():
    # un punct cu w <= 0 (dincolo de orizont) -> None
    h = [1, 0, 0, 0, 1, 0, 0, -2, 1]
    assert omografie.proiecteaza(h, 0.5, 0.8) is None
    assert omografie.proiecteaza(h, 0.5, 0.2) is not None


@pytest.fixture
def plan(tmp_path, monkeypatch):
    monkeypatch.setattr(harta, "FILE", str(tmp_path / "harta.json"))
    monkeypatch.setattr(harta, "DIR", str(tmp_path / "harta"))
    p = harta.plan_nou("A")
    harta.aseaza(p["id"], {"5": {"x": 0.5, "y": 0.5}, "6": {"x": 0.2, "y": 0.2}})
    return p["id"]


def test_calibrarea_pe_harta(plan):
    with pytest.raises(ValueError):
        harta.seteaza_calibrare(plan, 9, _perechi(COLTURI))           # nu e pe plan
    harta.seteaza_calibrare(plan, 5, _perechi(COLTURI))
    assert harta.lista()[0]["camere"]["5"]["calibrare"]["eroare"] < 1e-3
    # mutarea marcajului pastreaza calibrarea; clientul nu o poate scrie prin aseaza
    harta.aseaza(plan, {"5": {"x": 0.3, "y": 0.3, "calibrare": {"h": [0] * 9}},
                        "6": {"x": 0.1, "y": 0.1, "calibrare": {"h": [1] * 9}}})
    cams = harta.lista()[0]["camere"]
    assert cams["5"]["calibrare"]["eroare"] < 1e-3 and "calibrare" not in cams["6"]
    # detectiile: punctul de pe sol (mijlocul laturii de jos), fara cele filtrate
    stare = {"5": {"boxes": [{"x": 0.4, "y": 0.6, "w": 0.2, "h": 0.2, "lbl": "person", "conf": 0.9},
                             {"x": 0.4, "y": 0.6, "w": 0.2, "h": 0.2, "lbl": "person", "conf": 0.4, "filtru": "marime"}],
                   "boxes_age": 0.5},
             "6": {"boxes": [{"x": 0.4, "y": 0.6, "w": 0.2, "h": 0.2, "lbl": "car", "conf": 0.9}]}}
    pts = harta.detectii_pe_plan(plan, stare)
    ex = _pe_plan(0.5, 0.8)
    assert len(pts) == 1 and pts[0]["cam"] == 5 and pts[0]["lbl"] == "person"
    assert abs(pts[0]["x"] - ex[0]) < 1e-3 and abs(pts[0]["y"] - ex[1]) < 1e-3
    assert harta.detectii_pe_plan(plan, stare, visible={6}) == []    # contul nu vede camera 5
    # o imagine noua de plan sterge calibrarile
    harta.salveaza_imagine(plan, b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")
    assert "calibrare" not in harta.lista()[0]["camere"]["5"]
    harta.seteaza_calibrare(plan, 5, _perechi(COLTURI))
    harta.seteaza_calibrare(plan, 5, None)
    assert "calibrare" not in harta.lista()[0]["camere"]["5"]
