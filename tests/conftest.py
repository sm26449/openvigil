"""Fixture comun + protectia bazei de date la testare.

Jurnalul de evenimente e pe PostgreSQL din 2.0.0: fara un server Postgres (CI il porneste,
vezi .github/workflows/ci.yml), testele se SAR — aplicatia oricum nu ruleaza fara baza. Cand
PG e disponibil, golim tabelul `events` inainte de fiecare test ca sa fie izolat (baza e
partajata intre teste). Auditul (JSONL) ramane pe fisier si merge intr-un director temporar,
ca testele sa nu atinga niciodata fisierul real. (Lantul de custodie e si el pe PostgreSQL din
Faza 3, cu propria fixtura in test_lant/test_arhiva.)

PROTECTIE ANTI-PRODUCTIE: `_izolare` face `TRUNCATE events`. Productia, CI-ul si un PG de unica
folosinta au acelasi nume de baza (`openvigil`), acelasi user si acelasi host — nu se pot
deosebi dupa conninfo. De aceea testele refuza sa atinga o baza care NU e marcata explicit ca
baza de test prin variabila de mediu `OPENVIGIL_TEST_DB=1`. Marcajul il pune doar mediul de
test (CI il seteaza; PG-ul de unica folosinta il primeste la `docker run -e OPENVIGIL_TEST_DB=1`);
productia nu-l pune niciodata, deci `pytest` pointat din greseala spre productie se OPRESTE
inainte de orice `TRUNCATE`, nu sterge nimic.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import audit  # noqa: E402
import db  # noqa: E402
import jurnal  # noqa: E402

# verificam disponibilitatea PostgreSQL O SINGURA DATA pe sesiune (nu la fiecare test): fara
# server (ex. testele rulate in imaginea Docker, fara `db`) toata suita se sare in cateva
# secunde, nu blocheaza cate 3 s per test.
_PG = {"ok": None}


def _pg_disponibil():
    if _PG["ok"] is None:
        try:
            db.ping()
            _PG["ok"] = True
        except Exception:
            _PG["ok"] = False
    return _PG["ok"]


def _e_baza_de_test():
    """Adevarat doar daca mediul a confirmat explicit ca baza PG e de unica folosinta."""
    return os.environ.get("OPENVIGIL_TEST_DB") == "1"


def pytest_configure(config):
    """Rulat o data, inainte de colectare. Doua garzi, inainte ca vreun test sa atinga baza:

    1. anti-productie: daca PG raspunde dar baza NU e marcata ca de test, oprim TOT. Altfel
       `_izolare` ar face `TRUNCATE events` pe orice indica PG_* — inclusiv productia (~300k
       evenimente). Semnalul e explicit (OPENVIGIL_TEST_DB=1), nu o euristica pe nume/host.
    2. baza de test obligatorie in CI: daca marcajul e pus dar PG nu raspunde, esuam zgomotos
       (nu sarim toata suita) — altfel o cadere a serviciului Postgres in CI ar trece drept
       "0 teste, succes".
    """
    marcat = _e_baza_de_test()
    pg = _pg_disponibil()
    if marcat and not pg:
        raise pytest.UsageError(
            "OPENVIGIL_TEST_DB=1, deci se asteapta o baza PostgreSQL de test, dar baza nu "
            "raspunde (db.ping a esuat). Verifica serviciul Postgres de test."
        )
    if pg and not marcat:
        raise pytest.UsageError(
            "PG_* indica o baza PostgreSQL care NU e marcata ca baza de test, iar testele fac "
            "TRUNCATE pe tabelul `events`. Refuz, ca sa nu sterg o baza reala.\n"
            "Ruleaza pe o baza de UNICA FOLOSINTA si seteaza OPENVIGIL_TEST_DB=1 "
            "(ex. docker run -e OPENVIGIL_TEST_DB=1 -e PG_PORT=<port-test> ...)."
        )


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    # auditul (JSONL) ramane pe fisier -> director temporar (lantul de custodie e pe PG, cu
    # propria fixtura in test_lant/test_arhiva)
    monkeypatch.setattr(audit, "FILE", str(tmp_path / "changes.jsonl"))
    # jurnalul e pe PostgreSQL: sare daca nu e disponibil (ex. imaginea Docker, fara `db`),
    # altfel goleste `events`. Cand PG raspunde, pytest_configure a garantat deja ca baza e de
    # test (OPENVIGIL_TEST_DB=1), deci TRUNCATE-ul de mai jos e sigur.
    if not _pg_disponibil():
        pytest.skip("PostgreSQL indisponibil")
    jurnal.reset()
    jurnal.deschide()
    with db.conn() as c:
        c.execute("TRUNCATE events")
    yield
    jurnal.reset()
