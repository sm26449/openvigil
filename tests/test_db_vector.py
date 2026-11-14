"""Conexiunile din pool deschise inainte de `CREATE EXTENSION vector` (prima pornire, baza goala)
nu raman fara tipul `vector`: inregistrarea se reincearca la urmatoarea folosire."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db  # noqa: E402


class _Conexiune:
    pass


def test_inregistrarea_esuata_se_reincearca(monkeypatch):
    import pgvector.psycopg as pv
    incercari = []

    def picat(c):
        incercari.append("picat")
        raise RuntimeError("type vector not found")
    monkeypatch.setattr(pv, "register_vector", picat)
    c = _Conexiune()
    db._configure(c)                          # extensia inca nu exista
    assert c in db._fara_vector
    monkeypatch.setattr(pv, "register_vector", lambda c: incercari.append("ok"))
    db._asigura_vector(c)                     # extensia exista acum
    assert c not in db._fara_vector and incercari == ["picat", "ok"]
    db._asigura_vector(c)                     # o data inregistrata, nu se mai reia
    assert incercari == ["picat", "ok"]
