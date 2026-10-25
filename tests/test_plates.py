"""Teste pentru app/plates.py — normalizare, masini cunoscute, jurnal, cai sigure.

Nu ating modelul ONNX: doar logica pura. Fisierele merg intr-un director temporar.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import plates  # noqa: E402


@pytest.fixture(autouse=True)
def _plates_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(plates, "PLATES_DIR", str(tmp_path))
    monkeypatch.setattr(plates, "CROPS_DIR", str(tmp_path / "crops"))
    monkeypatch.setattr(plates, "LOG_FILE", str(tmp_path / "log.jsonl"))
    monkeypatch.setattr(plates, "KNOWN_FILE", str(tmp_path / "known.json"))
    plates._read_last.clear()


def test_normalize():
    assert plates.normalize("b 123-xyz") == "B123XYZ"
    assert plates.normalize("IF 12 ABC") == "IF12ABC"
    assert plates.normalize(" b-01-abc ") == "B01ABC"


def test_normalize_refuza_gunoiul():
    for rau in ("", "ab", "x" * 15, "!!!", None):
        with pytest.raises(ValueError):
            plates.normalize(rau)


def test_masini_cunoscute():
    assert plates.known() == {}
    p = plates.set_known("b 123 xyz", "duba lui Ion")
    assert p == "B123XYZ"
    assert plates.known() == {"B123XYZ": "duba lui Ion"}
    plates.set_known("B123XYZ", "alt nume")            # actualizare, nu duplicat
    assert plates.known() == {"B123XYZ": "alt nume"}
    plates.del_known("b-123-xyz")                       # stergerea normalizeaza si ea
    assert plates.known() == {}


def test_del_masina_inexistenta():
    with pytest.raises(ValueError):
        plates.del_known("B999ZZZ")


def test_set_known_fara_nume():
    with pytest.raises(ValueError):
        plates.set_known("B123XYZ", "  ")


def test_jurnal_scriere_si_citire_inversa():
    for i in range(5):
        plates._append_log({"t": f"2026-08-13 10:00:0{i}", "cam": 101,
                            "numar": f"B{i}23XYZ", "incredere": 0.9})
    r = plates.reads(limit=3)
    assert len(r) == 3
    assert r[0]["numar"] == "B423XYZ"   # cele mai noi primele


def test_jurnal_gol():
    assert plates.reads() == []


def test_clear_log():
    plates._append_log({"t": "x", "cam": 1, "numar": "B123XYZ", "incredere": 1})
    plates.clear_log()
    assert plates.reads() == []
    plates.clear_log()   # idempotent, nu crapa


def test_safe_path_blocheaza_traversarea():
    assert plates.safe_path("../../etc/passwd") is None
    ok = plates.safe_path("crops/2026-08-13/120000_B123XYZ.jpg")
    assert ok and ok.startswith(plates.PLATES_DIR)


def test_jurnalul_se_taie_la_plafon(monkeypatch):
    monkeypatch.setattr(plates, "LOG_MAX", 10)
    for i in range(12):
        plates._append_log({"t": str(i), "cam": 1, "numar": "B111AAA", "incredere": 1})
    assert sum(1 for _ in open(plates.LOG_FILE)) <= 10
