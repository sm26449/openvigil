"""Modul acasa/plecat/noapte (app/mod.py): stare, comutare idempotenta, noaptea automata
si programul saptamanal (timeline) — selectia tranzitiei, recuperarea si pastrarea manuala."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import mod  # noqa: E402
import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "FILE", str(tmp_path / "mod.json"))
    monkeypatch.setattr(mod, "PROGRAM_FILE", str(tmp_path / "mod_program.json"))
    anunturi = []
    monkeypatch.setattr(mod, "_anunta", lambda st: anunturi.append(st))
    cfg = {"mod_auto_noapte": True, "night_start": "22:00", "night_end": "06:00"}
    monkeypatch.setattr(settings, "get", lambda k: cfg.get(k))
    yield anunturi, cfg


# 2024-01-01 e luni (tm_wday 0); o folosim ca ancora pentru epoch-uri cu zi/ora stiute
def _epoch(y, mo, d, h, mi):
    return time.mktime((y, mo, d, h, mi, 0, 0, 0, -1))


_ZILNIC = {str(z): [{"h": "06:00", "mod": "acasa"}, {"h": "22:00", "mod": "noapte"}]
           for z in range(7)}


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


# ---- programul saptamanal (timeline) ----
def test_program_salvare_si_validare(_izolare):
    prg = mod.set_program({"activ": True, "zile": {"0": [{"h": "9:5", "mod": "noapte"}]}})
    assert prg["activ"] is True
    assert prg["zile"]["0"] == [{"h": "09:05", "mod": "noapte"}]   # normalizat HH:MM
    assert all(prg["zile"][str(z)] == [] for z in range(1, 7))
    with pytest.raises(ValueError):
        mod.set_program({"activ": True, "zile": {"0": [{"h": "25:00", "mod": "noapte"}]}})
    with pytest.raises(ValueError):
        mod.set_program({"activ": True, "zile": {"0": [{"h": "08:00", "mod": "vacanta"}]}})


def test_program_aplica_tranzitia_si_idempotent(_izolare):
    mod.set_program({"activ": True, "zile": _ZILNIC})
    # luni 23:00: cea mai recenta tranzitie e luni 22:00 -> noapte (recuperare: n-am prins exact 22:00)
    assert mod.tick(now=_epoch(2024, 1, 1, 23, 0)) == "noapte"
    assert mod.curent() == "noapte"
    # aceeasi tranzitie, un minut mai tarziu: deja aplicata -> nimic
    assert mod.tick(now=_epoch(2024, 1, 1, 23, 1)) is None
    # marti 06:30: tranzitia marti 06:00 -> acasa
    assert mod.tick(now=_epoch(2024, 1, 2, 6, 30)) == "acasa"
    assert mod.curent() == "acasa"


def test_program_pastreaza_schimbarea_manuala(_izolare):
    mod.set_program({"activ": True, "zile": _ZILNIC})
    mod.tick(now=_epoch(2024, 1, 1, 23, 0))          # -> noapte (luni 22:00 aplicata)
    mod.tick(now=_epoch(2024, 1, 2, 6, 30))          # -> acasa (marti 06:00 aplicata)
    mod.set("plecat", cine="stefan")                 # schimbare manuala intre tranzitii
    # marti 10:00: nicio tranzitie noua (ultima = marti 06:00, deja aplicata) -> ramane plecat
    assert mod.tick(now=_epoch(2024, 1, 2, 10, 0)) is None
    assert mod.curent() == "plecat"
    # marti 22:00: tranzitie noua -> noapte (programul invinge override-ul la ora lui)
    assert mod.tick(now=_epoch(2024, 1, 2, 22, 0)) == "noapte"


def test_program_inactiv_cade_pe_fereastra_noapte(_izolare):
    mod.set_program({"activ": False, "zile": _ZILNIC})
    # program inactiv -> comportamentul vechi (mod_auto_noapte + fereastra unica)
    assert mod.tick("23:30") == "noapte"
    assert mod.tick("06:30") == "acasa"
