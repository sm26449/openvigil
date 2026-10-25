"""Teste pentru app/incidents.py — coada, confirmarea, prioritizarea."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import incidents  # noqa: E402
import rules  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(incidents, "ACK_FILE", str(tmp_path / "ack.json"))
    monkeypatch.setattr(rules, "_is_night", lambda *a: True)


def _seed():
    # din 2026-09-10 detectiile brute (si person) sunt info — prezenta umana
    # e treaba EPISOADELOR; exemplul de "atentie" e un episod de tranzit nocturn
    rules._journal("detectie", 5, {"eticheta": "person"})           # info
    rules._journal("episod", 5, {"durata_sec": 5,
                                 "comportament": "a trecut prin cadru"})  # atentie
    rules._journal("persoana_necunoscuta", 7, {})                   # critic
    return [e["id"] for e in rules.journal()]


def test_feed_exclude_info_si_prioritizeaza_critic():
    _seed()
    f = incidents.feed()
    # info (gaina) nu e incident; critic e primul
    assert all(e["sev"] != "info" for e in f)
    assert f[0]["sev"] == "critic"
    assert {e["sev"] for e in f} == {"critic", "atentie"}


def test_set_state_si_annotate():
    ids = _seed()
    crit_id = next(e["id"] for e in rules.journal() if e["sev"] == "critic")
    incidents.set_state(crit_id, "confirmat", who="paznic")
    f = incidents.feed()
    crit = next(e for e in f if e["id"] == crit_id)
    assert crit["stare"] == "confirmat" and crit["tratat_de"] == "paznic"
    # doar_noi il exclude acum
    assert crit_id not in [e["id"] for e in incidents.feed(doar_noi=True)]


def test_set_state_fals_si_reredeschidere():
    _seed()
    eid = next(e["id"] for e in rules.journal() if e["sev"] == "atentie")
    incidents.set_state(eid, "fals")
    assert incidents.feed(doar_noi=True) and eid not in [e["id"] for e in incidents.feed(doar_noi=True)]
    incidents.set_state(eid, "nou")   # redeschidere
    assert eid in [e["id"] for e in incidents.feed(doar_noi=True)]


def test_set_state_invalid():
    with pytest.raises(ValueError):
        incidents.set_state("x", "rezolvat_cumva")


def test_counts():
    _seed()
    c = incidents.counts()
    assert c["noi"] == 2 and c["critic_noi"] == 1


def test_resolve_recent_auto_rezolva_offline():
    # o camera pica de doua ori (doua evenimente camera_offline netratate)
    rules._journal("camera_offline", 5, {})
    rules._journal("camera_offline", 5, {})
    rules._journal("camera_offline", 7, {})   # alta camera
    n = incidents.resolve_recent(5, "camera_offline")
    assert n == 2                              # ambele de pe cam5 rezolvate
    # nu mai apar ca netratate
    ack = incidents._load()
    off5 = [e for e in rules.journal(tip="camera_offline", cam=5)]
    assert all(ack.get(e["id"], {}).get("stare") == "confirmat" for e in off5)
    # camera 7 ramane netratata
    assert incidents.resolve_recent(7, "camera_offline") == 1


def test_filtru_pe_camere_vizibile():
    _seed()
    # operator care vede doar camera 5 -> nu vede incidentul critic de pe cam7
    f = incidents.feed(visible={5})
    assert all(e.get("cam") == 5 for e in f)
    assert incidents.counts(visible={5})["critic_noi"] == 0
