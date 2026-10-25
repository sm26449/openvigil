"""Teste pentru app/ask.py — parserul de intrebari si formularea raspunsului.

semantic.appearances se inlocuieste cu date sintetice; nu se atinge niciun model.
"""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ask  # noqa: E402
import semantic  # noqa: E402


def test_is_question():
    assert ask.is_question("cand a venit masina de gunoi?")
    assert ask.is_question("Când a venit mașina de gunoi")   # diacritice + fara '?'
    assert ask.is_question("de cate ori a aparut postasul")
    assert ask.is_question("ceva oarecare?")
    assert not ask.is_question("masina alba")
    assert not ask.is_question("om cu rucsac")
    assert not ask.is_question("")


def test_parse_concept_mod_fereastra():
    p = ask.parse("Când a venit mașina de gunoi ultima dată?")
    assert (p["concept"], p["mod"], p["days"]) == ("masina de gunoi", "cand", 30)
    assert p["label"] is None    # "masina de gunoi" NU e eticheta pura "masina"
    p = ask.parse("de cate ori a aparut postasul saptamana asta")
    assert p["concept"] == "postasul" and p["mod"] == "cate" and p["days"] == 7
    p = ask.parse("cand a trecut tractorul azi?")
    assert p["concept"] == "tractorul" and p["days"] == 1
    p = ask.parse("cine a fost la poarta ieri?")
    assert p["concept"] == "la poarta" and p["days"] == 2


def test_parse_eticheta_pura_si_pluralul():
    p = ask.parse("cate masini au trecut azi?")
    assert p["concept"] == "masini" and p["mod"] == "cate" and p["label"] == "car"
    assert ask.parse("cand a venit un caine?")["label"] == "dog"
    assert ask.parse("cati oameni au trecut ieri")["label"] == "person"


def test_answer_eticheta_pura_numara_din_jurnal(monkeypatch):
    import rules
    # izolare: fara track-uri reale din jurnalul viu (calea preferata de ask)
    monkeypatch.setattr(rules, "journal", lambda **kw: [])
    monkeypatch.setattr(rules, "episodes", lambda **kw: [
        {"inceput": time.strftime("%Y-%m-%d %H:%M:%S"), "sfarsit": "x",
         "cam": 14, "tip": "detectie", "detaliu": "car", "evenimente": 3, "durata": 60},
        {"inceput": time.strftime("%Y-%m-%d %H:%M:%S"), "sfarsit": "x",
         "cam": 5, "tip": "detectie", "detaliu": "car", "evenimente": 1, "durata": 0},
        {"inceput": time.strftime("%Y-%m-%d %H:%M:%S"), "sfarsit": "x",
         "cam": 14, "tip": "detectie", "detaliu": "dog", "evenimente": 1, "durata": 0},
    ])
    r = ask.answer("cate masini au trecut azi?", cam_names={14: "Strada"})
    assert "2 treceri" in r["raspuns"] and "jurnalul de detectii" in r["raspuns"]
    # filtrul de camera restrange la camera aleasa
    r = ask.answer("cate masini au trecut azi?", cam=14, cam_names={14: "Strada"})
    assert "1 treceri" in r["raspuns"] and "pe Strada" in r["raspuns"]


def test_answer_hibrid_filtreaza_pe_etichete_vehicul(monkeypatch):
    prins = {}
    def fake_app(concept, days=30, visible=None, gap=None, labels=None):
        prins.update(gap=gap, labels=labels)
        return [_grup("2026-08-15 09:12:33", scor=0.12)], 0.12
    monkeypatch.setattr(semantic, "appearances", fake_app)
    r = ask.answer("cate masini au trecut de culoare alba?")
    assert prins["labels"] == ask.VEHICLE_LABELS and prins["gap"] == 120
    assert "aproximativ" in r["raspuns"]


def _grup(inceput, camere=(4,), poze=3, scor=0.12):
    return {"inceput": inceput, "sfarsit": inceput, "camere": list(camere),
            "poze": poze, "scor": scor, "coperta": "/snapshots/x/y.jpg"}


def test_answer_cand(monkeypatch):
    monkeypatch.setattr(semantic, "appearances", lambda c, days=30, visible=None, gap=None, labels=None: (
        [_grup("2026-08-15 09:12:33"), _grup("2026-08-12 09:05:00")], 0.15))
    r = ask.answer("cand a venit masina de gunoi?", cam_names={4: "Poarta"})
    assert "Ultima data:" in r["raspuns"] and "Poarta" in r["raspuns"]
    assert "Inainte de asta:" in r["raspuns"]
    assert len(r["aparitii"]) == 2


def test_answer_numarare(monkeypatch):
    monkeypatch.setattr(semantic, "appearances", lambda c, days=30, visible=None, gap=None, labels=None: (
        [_grup("2026-08-15 09:12:33")] * 4, 0.15))
    r = ask.answer("de cate ori a venit masina de gunoi luna asta?")
    assert "4 aparitii" in r["raspuns"]


def test_answer_potrivire_slaba_avertizeaza(monkeypatch):
    monkeypatch.setattr(semantic, "appearances", lambda c, days=30, visible=None, gap=None, labels=None: (
        [_grup("2026-08-15 09:12:33", scor=0.07)], 0.07))
    r = ask.answer("cand a venit girafa?")
    assert "s-ar putea sa ma insel" in r["raspuns"]
    monkeypatch.setattr(semantic, "appearances", lambda c, days=30, visible=None, gap=None, labels=None: (
        [_grup("2026-08-15 09:12:33", scor=0.12)], 0.12))
    r = ask.answer("cand a venit masina de gunoi?")
    assert "insel" not in r["raspuns"]


def test_answer_negasit(monkeypatch):
    monkeypatch.setattr(semantic, "appearances",
                        lambda c, days=30, visible=None, gap=None, labels=None: ([], 0.02))
    r = ask.answer("cand a venit girafa?")
    assert "Nu am gasit" in r["raspuns"] and r["aparitii"] == []


def test_answer_fara_concept():
    r = ask.answer("cand a venit ultima data?")
    assert "Nu am inteles" in r["raspuns"]


def test_fmt_azi_si_zi_saptamana():
    azi = time.strftime("%Y-%m-%d")
    assert ask._fmt(f"{azi} 09:12:00") == "azi 09:12"
    assert ask._fmt("2026-08-11 14:30:00").startswith("marti 11.08")


def test_appearances_grupare_si_prag(monkeypatch, tmp_path):
    import numpy as np
    monkeypatch.setattr(semantic, "SEM_DIR", str(tmp_path))
    def unit(v):
        x = np.zeros(8, np.float32); x[:len(v)] = v
        return x / np.linalg.norm(x)
    # doua potriviri la 5 min (o aparitie), una a doua zi, si un zgomot sub prag
    semantic.shard_add("2026-08-14", np.stack([unit([1]), unit([1, 0.1]), unit([0, 1])]),
                       [{"fn": "a.jpg", "cam": 4, "label": "car", "time": "09:00:00"},
                        {"fn": "b.jpg", "cam": 5, "label": "car", "time": "09:05:00"},
                        {"fn": "z.jpg", "cam": 4, "label": "car", "time": "12:00:00"}])
    semantic.shard_add("2026-08-15", np.stack([unit([1, 0.05])]),
                       [{"fn": "c.jpg", "cam": 4, "label": "car", "time": "09:10:00"}])
    monkeypatch.setattr(semantic, "embed_text", lambda q: unit([1]))
    grupuri, top = semantic.appearances("orice", days=7)
    assert len(grupuri) == 2                       # z.jpg (ortogonal) nu intra
    assert grupuri[0]["inceput"].startswith("2026-08-15")   # cele mai noi primele
    assert grupuri[1]["camere"] == [4, 5] and grupuri[1]["poze"] == 2
    assert top > 0.99
