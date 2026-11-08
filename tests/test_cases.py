"""Teste pentru app/cases.py — CRUD-ul dosarelor de caz si al probelor (PostgreSQL).

Cazurile stau in `dosare` + `dosar_probe` (vezi app/cases.py). Testele cer o baza
PostgreSQL (ca test_probe / test_marcaje): se sar daca nu e disponibila, iar fixture-ul
GOLESTE tabelele — deci ruleaza in CI, nu peste o baza vie."""
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import cases  # noqa: E402
import probe  # noqa: E402
import db  # noqa: E402


@pytest.fixture(autouse=True)
def _pg():
    try:
        db.ping()
    except Exception as e:
        pytest.skip(f"PostgreSQL indisponibil: {e}")
    cases._init()
    probe._init()      # add_proba auto-protejeaza footage -> scrie in `probe`; cream tabelul
    with db.conn() as c:
        # dosar_probe inainte (FK spre dosare); probe fiindca add_proba auto-protejeaza footage
        c.execute("TRUNCATE dosar_probe, dosare, probe")
    yield


def test_create_si_lista():
    c = cases.create("Intrare noaptea", who="paznic", descriere="persoana la poarta")
    assert c["titlu"] == "Intrare noaptea" and c["creat_de"] == "paznic"
    assert c["descriere"] == "persoana la poarta" and c["probe"] == []
    lst = cases.lista()
    assert len(lst) == 1 and lst[0]["nr_probe"] == 0 and lst[0]["probe"] == []


def test_create_titlu_gol():
    with pytest.raises(ValueError):
        cases.create("   ")


def test_add_si_del_proba():
    c = cases.create("Caz")
    cid = c["id"]
    cases.add_proba(cid, 14, "20260825T000305Z", "20260825T000405Z", nota="masina")
    c2 = cases.add_proba(cid, 3, "20260825T010000Z", "20260825T010100Z")
    assert len(c2["probe"]) == 2
    # ordinea adaugarii se pastreaza
    assert c2["probe"][0]["cam"] == 14 and c2["probe"][0]["nota"] == "masina"
    assert c2["probe"][0]["start"] == "20260825T000305Z"
    pid = c2["probe"][0]["id"]
    assert cases.del_proba(cid, pid) is True
    assert len(cases.get(cid)["probe"]) == 1
    assert cases.del_proba(cid, "inexistent") is False


def test_add_proba_caz_inexistent():
    with pytest.raises(KeyError):
        cases.add_proba("nuexista", 1, "a", "b")


def test_del_proba_caz_inexistent():
    with pytest.raises(KeyError):
        cases.del_proba("nuexista", "x")


def test_delete_caz():
    cid = cases.create("De sters")["id"]
    assert cases.delete(cid) is True
    assert cases.get(cid) is None
    assert cases.delete(cid) is False


def test_delete_caz_sterge_probele():
    cid = cases.create("Cu probe")["id"]
    cases.add_proba(cid, 1, "20260101T000000Z", "20260101T000100Z")
    with db.conn() as c:
        assert c.execute("SELECT count(*) FROM dosar_probe WHERE dosar=%s", (cid,)).fetchone()[0] == 1
    cases.delete(cid)
    with db.conn() as c:   # CASCADE: nu raman probe orfane
        assert c.execute("SELECT count(*) FROM dosar_probe WHERE dosar=%s", (cid,)).fetchone()[0] == 0


def test_lista_ordonata_nou_primul():
    cases.create("primul")
    cases.create("al doilea")
    lst = cases.lista()
    # cel mai nou (creat ultimul) apare primul, independent de secunda de creare
    assert lst[0]["titlu"] == "al doilea" and lst[1]["titlu"] == "primul"


def test_persistenta():
    cid = cases.create("Persistent")["id"]
    cases.add_proba(cid, 14, "s", "e")
    # o citire noua vede aceeasi proba (persista in baza)
    assert cases.get(cid)["probe"][0]["cam"] == 14


def test_add_proba_concurent_nu_pierde():
    # mai multe fire adauga probe simultan, toate trebuie sa ramana (fiecare INSERT e atomic)
    cid = cases.create("Caz concurent")["id"]
    fire_n, per = 8, 20
    bariera = threading.Barrier(fire_n)
    erori = []

    def worker(k):
        try:
            bariera.wait()
            for i in range(per):
                cases.add_proba(cid, 1, f"2026{k:02d}{i:02d}T000000Z", f"2026{k:02d}{i:02d}T000100Z")
        except Exception as e:      # noqa: BLE001
            erori.append(e)

    fire = [threading.Thread(target=worker, args=(k,)) for k in range(fire_n)]
    for th in fire:
        th.start()
    for th in fire:
        th.join()
    assert not erori, erori
    assert len(cases.get(cid)["probe"]) == fire_n * per
