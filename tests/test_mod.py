"""Modul acasa/plecat/noapte (app/mod.py): stare, comutare idempotenta, noaptea automata."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import mod  # noqa: E402
import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "FILE", str(tmp_path / "mod.json"))
    anunturi = []
    monkeypatch.setattr(mod, "_anunta", lambda st: anunturi.append(st))
    cfg = {"mod_auto_noapte": True, "night_start": "22:00", "night_end": "06:00"}
    monkeypatch.setattr(settings, "get", lambda k: cfg.get(k))
    yield anunturi, cfg


def test_implicit_acasa_si_comutare(_izolare):
    anunturi, _ = _izolare
    assert mod.get()["mod"] == "acasa" and mod.curent() == "acasa"
    st = mod.set("plecat", cine="stefan", sursa="ui")
    assert st["mod"] == "plecat" and st["anterior"] == "acasa" and st["cine"] == "stefan"
    assert mod.get()["mod"] == "plecat" and mod.get()["de_cand"]
    assert len(anunturi) == 1
    assert mod.set("plecat")["mod"] == "plecat"          # acelasi mod: nimic
    assert len(anunturi) == 1
    with pytest.raises(ValueError):
        mod.set("vacanta")


def test_noaptea_automata(_izolare):
    anunturi, cfg = _izolare
    assert mod.tick("12:00") is None                      # zi, acasa: nimic
    assert mod.tick("23:30") == "noapte"
    assert mod.tick("23:31") is None                      # deja noapte
    assert mod.tick("06:30") == "acasa"
    mod.set("plecat")
    assert mod.tick("23:30") is None and mod.curent() == "plecat"   # plecat nu se atinge
    cfg["mod_auto_noapte"] = False
    mod.set("acasa")
    assert mod.tick("23:30") is None
