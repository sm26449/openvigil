#!/usr/bin/env python3
"""Stratul PostgreSQL (2.0.0) — un pool de conexiuni per proces, context-manageri
`conn()` / `tx()`, si `aplica(...)`, oglinda PG a lui app/schema.py.

Toate cele 4 servicii (go2rtc/web/record/ai) importa modulul asta; fiecare isi deschide
pool-ul lui, mic. Conexiunea vine din .env: PG_HOST, PG_PORT, PG_DB, PG_USER, PG_PASS.

SQL raw parametrizat (placeholder `%s`), fara ORM. Tipul `vector` (pgvector) se
inregistreaza pe fiecare conexiune noua; extensia se creeaza in `aplica()`.

    import db
    with db.conn() as c:                      # autocommit
        rows = c.execute("SELECT ... WHERE cam = %s", (cam,)).fetchall()
    with db.tx() as c:                        # tranzactie (rollback la exceptie)
        c.execute("INSERT ...", (...))
    db.aplica("jurnal", SCHEMA_SQL, MIGRARI)  # migrari numerotate, ca in schema.py
"""
import os
import threading
from contextlib import contextmanager

import logare

_POOL = None
_LOCK = threading.Lock()


def _conninfo():
    return (
        f"host={os.environ.get('PG_HOST', '127.0.0.1')} "
        f"port={os.environ.get('PG_PORT', '5433')} "
        f"dbname={os.environ.get('PG_DB', 'openvigil')} "
        f"user={os.environ.get('PG_USER', 'openvigil')} "
        f"password={os.environ.get('PG_PASS', '')}"
    )


def _configure(c):
    """Rulat la fiecare conexiune noua din pool: inregistreaza tipul `vector`.
    Daca extensia nu exista inca (inainte de prima `aplica`), mergem fara ea."""
    try:
        from pgvector.psycopg import register_vector
        register_vector(c)
    except Exception:
        pass


def pool():
    """Pool-ul procesului (lenes, thread-safe)."""
    global _POOL
    if _POOL is None:
        with _LOCK:
            if _POOL is None:
                from psycopg_pool import ConnectionPool
                _POOL = ConnectionPool(
                    _conninfo(),
                    min_size=1,
                    max_size=int(os.environ.get("PG_POOL", "4")),
                    kwargs={"autocommit": True},
                    configure=_configure,
                    open=True,
                )
                logare.info("[db] pool PostgreSQL deschis")
    return _POOL


@contextmanager
def conn():
    """Conexiune din pool, autocommit. Se intoarce in pool la iesire."""
    with pool().connection() as c:
        yield c


@contextmanager
def tx():
    """Tranzactie: commit la iesire normala, rollback la exceptie."""
    with pool().connection() as c:
        with c.transaction():
            yield c


def ping():
    """True daca baza raspunde; arunca daca nu se poate conecta."""
    with conn() as c:
        c.execute("SELECT 1")
    return True


def _versiune(c, nume):
    c.execute("CREATE TABLE IF NOT EXISTS schema_versiuni "
              "(nume TEXT PRIMARY KEY, versiune INTEGER NOT NULL)")
    r = c.execute("SELECT versiune FROM schema_versiuni WHERE nume = %s", (nume,)).fetchone()
    return r[0] if r else 0


def aplica(nume, schema_sql, migrari=None):
    """Schema initiala (idempotenta), apoi migrarile cu numar mai mare decat cea aplicata.
    Pastreaza tiparul din app/schema.py: versiuni per-modul in `schema_versiuni`, migrari
    numerotate, fiecare intr-o tranzactie. O baza noua primeste direct versiunea maxima.

        MIGRARI = {2: "ALTER TABLE events ADD COLUMN x TEXT", 3: functie(conn)}
    """
    migrari = migrari or {}
    tinta = max([1, *migrari.keys()])
    with pool().connection() as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS vector")
        if schema_sql and schema_sql.strip():
            c.execute(schema_sql)                      # fara parametri: psycopg accepta mai multe instructiuni
        actuala = _versiune(c, nume)
        if actuala == 0:
            c.execute("INSERT INTO schema_versiuni (nume, versiune) VALUES (%s, %s) "
                      "ON CONFLICT (nume) DO UPDATE SET versiune = EXCLUDED.versiune",
                      (nume, tinta))
            return tinta
        for v in sorted(migrari):
            if v <= actuala:
                continue
            pas = migrari[v]
            try:
                with c.transaction():
                    if callable(pas):
                        pas(c)
                    else:
                        c.execute(pas)
                    c.execute("INSERT INTO schema_versiuni (nume, versiune) VALUES (%s, %s) "
                              "ON CONFLICT (nume) DO UPDATE SET versiune = EXCLUDED.versiune",
                              (nume, int(v)))
            except Exception:
                raise
            logare.info(f"[db] {nume}: migrat la versiunea {v}")
            actuala = v
    return actuala
