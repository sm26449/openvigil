"""Teste pentru app/track.py — bookkeeping-ul de urmarire (fara model, fara camera)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import track  # noqa: E402


def test_directie():
    assert track.directie(200, 10) == "stanga→dreapta"
    assert track.directie(-200, 10) == "dreapta→stanga"
    assert track.directie(5, 120) == "sus→jos"
    assert track.directie(5, -120) == "jos→sus"
    assert track.directie(10, 10) == "stationar"


def test_track_confirmat_cu_durata_si_directie():
    t = track.Tracker(14)
    for i in range(8):   # 8 cadre, se misca spre dreapta
        t.update([(1, "car", 100 + i * 30, 200)], now=100.0 + i * 0.25)
    gata = t.expire(now=100.0 + 8 * 0.25 + track.LOST_TTL + 0.1)
    assert len(gata) == 1
    obj = gata[0]
    assert obj["eticheta"] == "car" and obj["cadre"] == 8
    assert obj["directie"] == "stanga→dreapta"
    assert obj["durata"] == 1.8   # 7 intervale x 0.25s = 1.75, rotunjit la o zecimala
    assert t.tracks == {}   # inchis si scos


def test_aparitiile_scurte_nu_se_numara():
    t = track.Tracker(14)
    for i in range(track.MIN_FRAMES - 1):   # sub pragul de confirmare
        t.update([(2, "person", 100, 100 + i)], now=50.0 + i * 0.25)
    assert t.expire(now=200.0) == []


def test_trackul_viu_nu_expira():
    t = track.Tracker(14)
    for i in range(10):
        t.update([(3, "dog", 100 + i, 100)], now=10.0 + i * 0.25)
    # inca in cadru (ultimul update recent) -> nu se inchide
    assert t.expire(now=10.0 + 10 * 0.25 + 0.5) == []
    assert 3 in t.tracks


def test_doua_masini_consecutive_sunt_doua_obiecte():
    t = track.Tracker(14)
    for i in range(8):
        t.update([(10, "car", 50 + i * 40, 200)], now=0.0 + i * 0.25)
    # prima iese; a doua intra la 4 secunde (ID nou de la tracker)
    for i in range(8):
        t.update([(11, "car", 50 + i * 40, 210)], now=6.0 + i * 0.25)
    gata = t.expire(now=6.0 + 8 * 0.25 + track.LOST_TTL + 0.1)
    assert len(gata) == 2   # exact doua, nu un episod comun


def test_ask_prefera_numararea_din_urmarire(monkeypatch):
    import time as _time
    import ask
    import rules
    acum = _time.strftime("%Y-%m-%d %H:%M:%S")
    def jurnal_fals(limit=200, tip=None, cam=None, day=None):
        if tip == "obiect":
            return [{"t": acum, "tip": "obiect", "cam": 14, "eticheta": "car",
                     "directie": "stanga→dreapta", "durata": 3.2, "cadre": 12}] * 5
        return []
    monkeypatch.setattr(rules, "journal", jurnal_fals)
    monkeypatch.setattr(rules, "episodes",
                        lambda **kw: pytest.fail("trebuia numarat din urmarire"))
    r = ask.answer("cate masini au trecut azi?", cam_names={14: "Strada"})
    assert "exact 5" in r["raspuns"] and "bucata cu bucata" in r["raspuns"]
    assert "stanga→dreapta" in r["raspuns"]
