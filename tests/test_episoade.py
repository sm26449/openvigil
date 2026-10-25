"""Episoadele de persoana (E2): o prezenta = UN eveniment-rezumat, nu un sir de cadre."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import episoade  # noqa: E402


@pytest.fixture
def emise(monkeypatch):
    out = []
    import hooks
    monkeypatch.setattr(hooks, "emit", lambda tip, cam=None, **d: out.append((tip, cam, d)))
    monkeypatch.setattr(episoade, "_ep", {})
    monkeypatch.setattr(episoade, "_nume", lambda cam, t0, t1: [])
    monkeypatch.setattr(episoade, "_semnalmente", lambda cam, t0, t1: None)
    monkeypatch.setattr(episoade.ai_analytics, "zones", lambda cid: [])
    return out


def test_episodul_se_inchide_dupa_gap(emise):
    episoade.cadru(7, [(0.5, 0.5)], 1000.0)
    episoade.cadru(7, [(0.5, 0.6)], 1010.0)
    episoade.tick(7, 1010.0 + episoade.GAP - 1)
    assert emise == []                                  # inca in gratie
    episoade.tick(7, 1010.0 + episoade.GAP + 1)
    assert len(emise) == 1
    tip, cam, d = emise[0]
    assert (tip, cam) == ("episod", 7)
    assert d["durata_sec"] == 10
    assert d["persoane_max"] == 1
    assert "o persoana necunoscuta" in d["text"]


def test_mai_multe_persoane_simultan(emise):
    episoade.cadru(7, [(0.2, 0.5), (0.7, 0.5)], 1000.0)
    episoade.track_inchis(7, "stanga→dreapta", 1005.0)
    episoade.tick(7, 1000.0 + episoade.GAP + 1)
    d = emise[0][2]
    assert d["persoane_max"] == 2
    assert "2 persoane simultan" in d["text"]
    assert d["directii"] == ["stanga→dreapta"]


def test_zonele_traversate(emise, monkeypatch):
    monkeypatch.setattr(episoade.ai_analytics, "zones",
                        lambda cid: [{"name": "poarta",
                                      "poly": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]}])
    episoade.cadru(7, [(0.5, 0.5)], 1000.0)            # in poligon
    episoade.cadru(7, [(0.9, 0.9)], 1002.0)            # iesit din el
    episoade.tick(7, 1002.0 + episoade.GAP + 1)
    d = emise[0][2]
    assert d["zone"] == ["poarta"]
    assert "zone: poarta" in d["text"]


def test_numele_intra_in_rezumat(emise, monkeypatch):
    monkeypatch.setattr(episoade, "_nume", lambda cam, t0, t1: ["rezident-1"])
    episoade.cadru(101, [(0.5, 0.5)], 1000.0)
    episoade.tick(101, 1000.0 + episoade.GAP + 1)
    d = emise[0][2]
    assert d["nume"] == ["rezident-1"]
    assert d["text"].startswith("rezident-1")


def test_revenirea_in_gap_prelungeste_acelasi_episod(emise):
    episoade.cadru(7, [(0.5, 0.5)], 1000.0)
    episoade.tick(7, 1030.0)                           # sub GAP — nu inchide
    episoade.cadru(7, [(0.5, 0.5)], 1040.0)            # omul revine
    episoade.tick(7, 1040.0 + episoade.GAP + 1)
    assert len(emise) == 1
    assert emise[0][2]["durata_sec"] == 40             # t0 ramane primul


def test_comportament_a_trecut():
    # drum aproape drept, scurt: tranzit
    trail = [(0.1, 0.5), (0.3, 0.5), (0.5, 0.5), (0.8, 0.5)]
    assert episoade.comportament(15, trail, {}) == "a trecut prin cadru"


def test_comportament_s_a_invartit():
    # drum lung, deplasare neta mica: du-te-vino
    trail = [(0.2, 0.5), (0.8, 0.5), (0.2, 0.5), (0.8, 0.5), (0.25, 0.5)]
    assert episoade.comportament(120, trail, {}) == "s-a invartit prin cadru"


def test_comportament_a_zabovit():
    trail = [(0.5, 0.5), (0.52, 0.5), (0.5, 0.52), (0.51, 0.5)]
    assert episoade.comportament(90, trail, {}) == "a zabovit pe loc"


def test_comportament_zona_bate_restul():
    assert episoade.comportament(120, [(0.1, 0.1), (0.9, 0.9)],
                                 {"poarta": 75.0}) == "a stat 75s la poarta"


def test_traseul_si_dwell_intra_in_eveniment(emise, monkeypatch):
    monkeypatch.setattr(episoade.ai_analytics, "zones",
                        lambda cid: [{"name": "usa",
                                      "poly": [[0.4, 0.4], [0.6, 0.4], [0.6, 0.6], [0.4, 0.6]]}])
    episoade.cadru(7, [(0.1, 0.5)], 1000.0)
    episoade.cadru(7, [(0.5, 0.5)], 1040.0)
    # track incheiat: 21 puncte, majoritatea IN zona "usa" -> dwell mare acolo
    trail = [(0.1, 0.5)] + [(0.5, 0.5)] * 20
    episoade.track_inchis(7, "stanga→dreapta", 1040.0, trail=trail, durata=40.0)
    episoade.tick(7, 1040.0 + episoade.GAP + 1)
    d = emise[0][2]
    assert d["comportament"].startswith("a stat") and "usa" in d["comportament"]
    assert d["traseu"][0] == [0.1, 0.5] and d["traseu"][-1] == [0.5, 0.5]
    assert d["comportament"] in d["text"]


def test_subesantionarea_pastreaza_capetele():
    trail = [(i / 99, 0.5) for i in range(100)]
    mic = episoade._subesantion(trail, 10)
    assert len(mic) == 10
    assert mic[0] == [0.0, 0.5] and mic[-1] == [1.0, 0.5]


def test_severitatea_episodului():
    import rules
    assert rules.severity("episod", {"nume": ["rezident-1"]}, False) == "info"
    assert rules.severity("episod", {"nume": []}, False) == "atentie"
    assert rules.severity("episod", {"nume": []}, True) == "critic"
    # pietonul care doar TRECE (scurt) nu e "omul pe alee noaptea" — primul episod
    # real (1s, cam14, 22:06) iesea critic si ar fi spamat triajul nocturn
    scurt = {"nume": [], "durata_sec": 1, "comportament": "a trecut prin cadru"}
    assert rules.severity("episod", scurt, True) == "atentie"
    assert rules.severity("episod", scurt, False) == "info"
    # traversarea normala de strada ia 10-20s (episodul real de la 23:08: 14s)
    mediu = {"nume": [], "durata_sec": 14, "comportament": "a trecut prin cadru"}
    assert rules.severity("episod", mediu, True) == "atentie"
    # dar zabovirea ramane grava noaptea, oricat de... zabovita
    lung = {"nume": [], "durata_sec": 120, "comportament": "a zabovit pe loc"}
    assert rules.severity("episod", lung, True) == "critic"
