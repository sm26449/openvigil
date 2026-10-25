"""Teste pentru app/rules.py — validare, potrivire, interval orar, cooldown, jurnal.

Actiunile nu se executa: _run_actions se inlocuieste cu un colector. Fisierele merg
intr-un director temporar (jurnalul SQLite: prin conftest).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import rules  # noqa: E402


@pytest.fixture(autouse=True)
def _rules_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "RULES_FILE", str(tmp_path / "rules.json"))
    monkeypatch.setattr(rules, "LAST_FIRE_FILE", str(tmp_path / "rule_last_fire.json"))
    rules._last_fire.clear()


def _regula(**over):
    r = {"nume": "test", "activ": True,
         "declansator": {"tip": "detectie", "valoare": "person"},
         "camere": [], "interval": None, "cooldown": 60,
         "actiuni": [{"tip": "notificare"}]}
    r.update(over)
    return r


def test_validate_si_upsert():
    r = rules.upsert(_regula())
    assert r["id"] and r["cooldown"] == 60
    assert len(rules.load()) == 1
    r2 = rules.upsert({**_regula(nume="alt nume"), "id": r["id"]})
    assert len(rules.load()) == 1 and rules.load()[0]["nume"] == "alt nume"


def test_validate_refuza_gunoiul():
    with pytest.raises(ValueError):
        rules.validate(_regula(declansator={"tip": "inexistent"}))
    with pytest.raises(ValueError):
        rules.validate(_regula(nume="  "))
    with pytest.raises(ValueError):
        rules.validate(_regula(actiuni=[]))
    with pytest.raises(ValueError):
        rules.validate(_regula(actiuni=[{"tip": "rachete"}]))
    with pytest.raises(ValueError):
        rules.validate(_regula(interval=["25:00", "06:00"]))


def test_delete():
    r = rules.upsert(_regula())
    rules.delete(r["id"])
    assert rules.load() == []
    with pytest.raises(ValueError):
        rules.delete("nu-exista")


def test_interval_peste_miezul_noptii():
    assert rules._in_interval(["23:00", "06:00"], "23:30")
    assert rules._in_interval(["23:00", "06:00"], "03:00")
    assert not rules._in_interval(["23:00", "06:00"], "12:00")
    assert rules._in_interval(["08:00", "17:00"], "12:00")
    assert not rules._in_interval(["08:00", "17:00"], "18:00")
    assert rules._in_interval(None, "12:00")   # fara interval = oricand


def test_value_match_detectie_si_persoana():
    assert rules._value_match("detectie", "person", {"eticheta": "person"})
    assert not rules._value_match("detectie", "dog", {"eticheta": "person"})
    assert rules._value_match("detectie", "", {"eticheta": "orice"})
    assert rules._value_match("persoana", "ion", {"nume": "ion"})
    assert not rules._value_match("persoana", "ion", {"nume": "ana"})


def test_value_match_numar():
    assert rules._value_match("numar", "b123xyz", {"numar": "B123XYZ"})
    assert rules._value_match("numar", "cunoscut", {"numar": "B1", "nume": "duba"})
    assert not rules._value_match("numar", "cunoscut", {"numar": "B1"})
    assert rules._value_match("numar", "necunoscut", {"numar": "B1"})
    assert not rules._value_match("numar", "necunoscut", {"numar": "B1", "nume": "duba"})


def test_value_match_alarma_camera_pe_subtip():
    assert rules._value_match("alarma_camera", "VMD", {"tip": "VMD"})
    assert not rules._value_match("alarma_camera", "VMD", {"tip": "linedetection"})
    assert rules._value_match("alarma_camera", "", {"tip": "orice"})


def test_journal_subtipul_nu_suprascrie_tipul():
    rules.on_event("alarma_camera", 3, {"tip": "linedetection", "moment_camera": "x"})
    e = rules.journal()[0]
    assert e["tip"] == "alarma_camera" and e["subtip"] == "linedetection"


def test_vmd_nu_se_jurnalizeaza():
    # VMD (motion brut) e zgomot ~15k/zi -> nu-l scriem in jurnal, ca evenimentele reale
    # sa nu fie impinse afara. Alte subtipuri de alarma_camera (linie/zona) raman.
    rules.on_event("alarma_camera", 3, {"tip": "VMD", "moment_camera": "x"})
    assert rules.journal() == []
    rules.on_event("alarma_camera", 3, {"tip": "fielddetection"})
    assert len(rules.journal()) == 1 and rules.journal()[0]["subtip"] == "fielddetection"


def test_matches_camere_si_activ():
    r = rules.validate(_regula(camere=[101]))
    assert rules.matches(r, "detectie", 101, {"eticheta": "person"}, "12:00")
    assert not rules.matches(r, "detectie", 102, {"eticheta": "person"}, "12:00")
    r2 = rules.validate(_regula(activ=False))
    assert not rules.matches(r2, "detectie", 101, {"eticheta": "person"}, "12:00")


def test_matches_interval():
    r = rules.validate(_regula(interval=["23:00", "06:00"]))
    assert rules.matches(r, "detectie", 1, {"eticheta": "person"}, "23:30")
    assert not rules.matches(r, "detectie", 1, {"eticheta": "person"}, "12:00")


def test_matches_zile_saptamana():
    r = rules.validate(_regula(zile=[5, 6]))   # doar weekend
    assert rules.matches(r, "detectie", 1, {"eticheta": "person"}, "12:00", weekday=6)
    assert not rules.matches(r, "detectie", 1, {"eticheta": "person"}, "12:00", weekday=2)
    # toate cele 7 zile bifate = fara restrictie (se normalizeaza la lista goala)
    r7 = rules.validate(_regula(zile=[0, 1, 2, 3, 4, 5, 6]))
    assert r7["zile"] == []


def test_matches_grupuri_ca_zone():
    r = rules.validate(_regula(grupuri=["Curte"]))
    assert rules.matches(r, "detectie", 5, {"eticheta": "person"}, "12:00",
                         cam_group="Curte")
    assert not rules.matches(r, "detectie", 5, {"eticheta": "person"}, "12:00",
                             cam_group="Poarta")
    # camerele si grupurile se combina cu SAU
    r2 = rules.validate(_regula(camere=[101], grupuri=["Curte"]))
    assert rules.matches(r2, "detectie", 101, {"eticheta": "person"}, "12:00",
                         cam_group="Poarta")     # camera potrivita, grupul nu
    assert rules.matches(r2, "detectie", 7, {"eticheta": "person"}, "12:00",
                         cam_group="Curte")      # grupul potrivit, camera nu
    assert not rules.matches(r2, "detectie", 7, {"eticheta": "person"}, "12:00",
                             cam_group="Poarta")


def test_actiunea_mqtt_e_valida():
    r = rules.validate(_regula(actiuni=[{"tip": "mqtt", "topic": "lumini/curte",
                                         "payload": "ON"}]))
    assert r["actiuni"][0]["topic"] == "lumini/curte"


def test_on_event_cooldown(monkeypatch):
    fired = []
    monkeypatch.setattr(rules, "_run_actions",
                        lambda r, t, c, d: fired.append(r["nume"]))
    # threadul se porneste cu target=_run_actions; il facem sincron
    class FakeThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._t, self._a = target, args
        def start(self):
            self._t(*self._a)
    monkeypatch.setattr(rules.threading, "Thread", FakeThread)
    rules.upsert(_regula(cooldown=60))
    rules.on_event("detectie", 1, {"eticheta": "person"})
    rules.on_event("detectie", 1, {"eticheta": "person"})   # sub cooldown -> nu
    assert fired == ["test"]


def test_on_event_jurnalizeaza_tot():
    rules.on_event("zgomot", 5, {"motiv": "prag depasit", "nivel_db": -20})
    rules.on_event("detectie", 1, {"eticheta": "dog"})
    j = rules.journal()
    assert len(j) == 2 and j[0]["tip"] == "detectie" and j[1]["cam"] == 5


def test_journal_filtre():
    rules.on_event("detectie", 1, {"eticheta": "dog"})
    rules.on_event("detectie", 2, {"eticheta": "person"})
    rules.on_event("zgomot", 1, {"motiv": "x"})
    assert len(rules.journal(tip="detectie")) == 2
    assert len(rules.journal(cam=1)) == 2
    assert len(rules.journal(tip="zgomot", cam=1)) == 1
    azi = rules.journal()[0]["t"][:10]
    assert len(rules.journal(day=azi)) == 3
    assert rules.journal(day="1999-01-01") == []


def _ev(t, tip="detectie", cam=1, **extra):
    rules.jurnal.insert({"t": t, "tip": tip, "cam": cam, **extra})


def test_episoade_grupeaza_cu_durata(monkeypatch):
    monkeypatch.setattr(rules.settings, "get", lambda k: 300)
    for s in ("10:00:00", "10:01:00", "10:03:30"):     # sub 5 min intre ele
        _ev(f"2026-08-14 {s}", eticheta="dog")
    eps = rules.episodes()
    assert len(eps) == 1
    e = eps[0]
    assert e["inceput"] == "2026-08-14 10:00:00"
    assert e["sfarsit"] == "2026-08-14 10:03:30"
    assert e["durata"] == 210 and e["evenimente"] == 3 and e["detaliu"] == "dog"


def test_episoade_pauza_mare_rupe_episodul(monkeypatch):
    monkeypatch.setattr(rules.settings, "get", lambda k: 300)
    _ev("2026-08-14 10:00:00", eticheta="dog")
    _ev("2026-08-14 11:00:00", eticheta="dog")        # la o ora -> episod nou
    eps = rules.episodes()
    assert len(eps) == 2
    assert eps[0]["inceput"] == "2026-08-14 11:00:00"  # cele mai noi primele


def test_episoade_chei_diferite_raman_separate(monkeypatch):
    monkeypatch.setattr(rules.settings, "get", lambda k: 300)
    _ev("2026-08-14 10:00:00", eticheta="dog")
    _ev("2026-08-14 10:00:30", eticheta="person")      # alt detaliu, acelasi moment
    _ev("2026-08-14 10:01:00", cam=2, eticheta="dog")  # alta camera
    assert len(rules.episodes()) == 3


def test_episoade_un_singur_eveniment_are_durata_zero(monkeypatch):
    monkeypatch.setattr(rules.settings, "get", lambda k: 300)
    _ev("2026-08-14 10:00:00", eticheta="dog")
    e = rules.episodes()[0]
    assert e["durata"] == 0 and e["evenimente"] == 1


def test_evenimentul_regula_nu_declanseaza_reguli(monkeypatch):
    fired = []
    monkeypatch.setattr(rules, "_run_actions",
                        lambda r, t, c, d: fired.append(1))
    rules.upsert(_regula())
    rules.on_event("regula", 1, {"regula": "x"})
    assert fired == []


# ---- modul acasa/plecat/noapte ----
def test_regula_restransa_la_moduri():
    r = rules.validate(_regula(moduri=["plecat", "noapte", "altceva"]))
    assert r["moduri"] == ["noapte", "plecat"]
    assert rules.matches(r, "detectie", 1, {"eticheta": "person"}, mod_curent="plecat")
    assert not rules.matches(r, "detectie", 1, {"eticheta": "person"}, mod_curent="acasa")
    # toate trei = fara restrictie
    assert rules.validate(_regula(moduri=["acasa", "plecat", "noapte"]))["moduri"] == []
    assert rules.validate(_regula())["moduri"] == []


def test_declansator_si_actiune_mod():
    r = rules.validate(_regula(declansator={"tip": "mod", "valoare": "plecat"},
                               actiuni=[{"tip": "mod", "mod": "noapte"}]))
    assert rules.matches(r, "mod", None, {"mod": "plecat"}, mod_curent="plecat")
    assert not rules.matches(r, "mod", None, {"mod": "acasa"}, mod_curent="acasa")
    with pytest.raises(ValueError):
        rules.validate(_regula(actiuni=[{"tip": "mod"}]))
