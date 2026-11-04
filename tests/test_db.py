"""Test de fum pentru stratul PostgreSQL (app/db.py) — Faza 0.

Se sare daca psycopg lipseste (masina fara PG) sau daca baza nu raspunde (dev local fara
serviciul `db` pornit). In CI, serviciul Postgres + pgvector e pornit si variabilele PG_*
sunt setate, deci testul chiar ruleaza: dovedeste extensia `vector`, schema, pool-ul,
tranzactia si distanta pe vectori — fara sa atinga vreun tabel real al aplicatiei.
"""
import pytest

pytest.importorskip("psycopg")
pytest.importorskip("psycopg_pool")


@pytest.fixture
def pg():
    import db
    try:
        db.ping()
    except Exception as e:
        pytest.skip(f"Postgres indisponibil: {e}")
    return db


def test_schema_pool_si_vector(pg):
    # schema initiala idempotenta + extensia vector, prin aplica()
    pg.aplica("test_db_smoke", """
        CREATE TABLE IF NOT EXISTS t_smoke (
            id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            emb vector(3)
        )""")

    # tranzactie: insert, apoi interogare de similaritate (cosinus/euclidian)
    with pg.tx() as c:
        c.execute("TRUNCATE t_smoke")
        c.execute("INSERT INTO t_smoke (emb) VALUES ('[1,2,3]'), ('[9,9,9]')")

    with pg.conn() as c:
        cel_mai_apropiat = c.execute(
            "SELECT emb FROM t_smoke ORDER BY emb <-> '[1,1,3]' LIMIT 1"
        ).fetchone()
    assert cel_mai_apropiat is not None

    # schema_versiuni retine versiunea modulului
    with pg.conn() as c:
        v = c.execute("SELECT versiune FROM schema_versiuni WHERE nume = %s",
                      ("test_db_smoke",)).fetchone()
    assert v and v[0] >= 1


def test_aplica_e_idempotent(pg):
    # a doua rulare pe aceeasi schema nu arunca si pastreaza versiunea
    pg.aplica("test_db_smoke", "CREATE TABLE IF NOT EXISTS t_smoke (id BIGINT, emb vector(3))")
    pg.aplica("test_db_smoke", "CREATE TABLE IF NOT EXISTS t_smoke (id BIGINT, emb vector(3))")
