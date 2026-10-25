"""Teste pentru app/cases.py — CRUD-ul dosarelor de caz si al probelor."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import cases  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cases, "CASES_FILE", str(tmp_path / "cases.json"))


def test_create_si_lista():
    c = cases.create("Intrare noaptea", who="paznic", descriere="persoana la poarta")
    assert c["titlu"] == "Intrare noaptea" and c["creat_de"] == "paznic"
    assert c["probe"] == []
    lst = cases.lista()
    assert len(lst) == 1 and lst[0]["nr_probe"] == 0


def test_create_titlu_gol():
    with pytest.raises(ValueError):
        cases.create("   ")


def test_add_si_del_proba():
    c = cases.create("Caz")
    cid = c["id"]
    cases.add_proba(cid, 14, "20260825T000305Z", "20260825T000405Z", nota="masina")
    c2 = cases.add_proba(cid, 3, "20260825T010000Z", "20260825T010100Z")
    assert len(c2["probe"]) == 2
    assert c2["probe"][0]["cam"] == 14 and c2["probe"][0]["nota"] == "masina"
    pid = c2["probe"][0]["id"]
    assert cases.del_proba(cid, pid) is True
    assert len(cases.get(cid)["probe"]) == 1
    assert cases.del_proba(cid, "inexistent") is False


def test_add_proba_caz_inexistent():
    with pytest.raises(KeyError):
        cases.add_proba("nuexista", 1, "a", "b")


def test_delete_caz():
    cid = cases.create("De sters")["id"]
    assert cases.delete(cid) is True
    assert cases.get(cid) is None
    assert cases.delete(cid) is False


def test_lista_ordonata_nou_primul():
    cases.create("primul")
    cases.create("al doilea")
    lst = cases.lista()
    # cel mai nou (creat ultimul) apare primul
    assert lst[0]["titlu"] == "al doilea"


def test_persistenta_intre_incarcari():
    cid = cases.create("Persistent")["id"]
    cases.add_proba(cid, 14, "s", "e")
    # o "noua sesiune" citeste acelasi fisier
    assert cases.get(cid)["probe"][0]["cam"] == 14
