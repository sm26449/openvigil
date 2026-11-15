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
import weakref
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


# conexiunile deschise INAINTE de `CREATE EXTENSION vector` (prima pornire pe o baza goala) n-au
# tipul inregistrat: vectorii cititi prin ele veneau ca octeti bruti. Le tinem minte si reincercam
# la urmatoarea folosire (prins de CI-ul rulat pe o baza noua, 2026-11-15).
_fara_vector = weakref.WeakSet()


def _inregistreaza_vector(c):
    try:
        from pgvector.psycopg import register_vector
        register_vector(c)
        _fara_vector.discard(c)
        return True
    except Exception:
        _fara_vector.add(c)
        return False


def _configure(c):
    """Rulat la fiecare conexiune noua din pool: inregistreaza tipul `vector`.
    Daca extensia nu exista inca (inainte de prima `aplica`), conexiunea se reincearca la folosire."""
    _inregistreaza_vector(c)


def _asigura_vector(c):
    if c in _fara_vector:
        _inregistreaza_vector(c)


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
                    kwargs={"autocommit": True, "connect_timeout": 10},
                    configure=_configure,
                    open=True,
                )
                logare.info("[db] pool PostgreSQL deschis")
                if os.environ.get("PG_PASS") in ("", "changeme"):
                    # parola din .env.example e publica in depozit (audit S8)
                    logare.error("[db] PG_PASS e goala sau cea din exemplu (changeme): schimb-o "
                                 "in PostgreSQL (ALTER USER) si in .env — vezi install.sh")
    return _POOL


@contextmanager
def conn():
    """Conexiune din pool, autocommit. Se intoarce in pool la iesire."""
    with pool().connection() as c:
        _asigura_vector(c)
        yield c


@contextmanager
def tx():
    """Tranzactie: commit la iesire normala, rollback la exceptie."""
    with pool().connection() as c:
        _asigura_vector(c)
        with c.transaction():
            yield c


def ping():
    """True daca baza raspunde; arunca REPEDE (connect_timeout scurt) daca nu se poate conecta.
    Conexiune directa, NU prin pool — altfel `.connection()` ar astepta timeout-ul pool-ului
    (~30 s) la fiecare apel cand baza e jos (ex. testele in imagine, fara Postgres)."""
    import psycopg
    with psycopg.connect(_conninfo(), connect_timeout=3, autocommit=True) as c:
        c.execute("SELECT 1")
    return True


def _lock_key(nume):
    """Cheie bigint stabila (independenta de PYTHONHASHSEED) pentru advisory lock-ul per modul."""
    import hashlib
    return int.from_bytes(hashlib.sha256(nume.encode()).digest()[:8], "big", signed=True)


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
        # advisory lock la nivel de sesiune, per modul: cele 4 procese (go2rtc/web/record/ai)
        # pornesc deodata si pot apela aplica() pentru acelasi modul in acelasi timp — fara lock,
        # doua ar citi versiunea veche si ar aplica aceeasi migrare. Se elibereaza in finally (sau
        # la inchiderea sesiunii, daca procesul moare).
        key = _lock_key(nume)
        c.execute("SELECT pg_advisory_lock(%s)", (key,))
        try:
            c.execute("CREATE EXTENSION IF NOT EXISTS vector")
            _asigura_vector(c)
            if schema_sql and schema_sql.strip():
                c.execute(schema_sql)                  # fara parametri: psycopg accepta mai multe instructiuni
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
                with c.transaction():
                    if callable(pas):
                        pas(c)
                    else:
                        c.execute(pas)
                    c.execute("INSERT INTO schema_versiuni (nume, versiune) VALUES (%s, %s) "
                              "ON CONFLICT (nume) DO UPDATE SET versiune = EXCLUDED.versiune",
                              (nume, int(v)))
                logare.info(f"[db] {nume}: migrat la versiunea {v}")
                actuala = v
            return actuala
        finally:
            c.execute("SELECT pg_advisory_unlock(%s)", (key,))
