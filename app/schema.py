#!/usr/bin/env python3
"""Versiunea schemei in bazele SQLite, ca un upgrade sa poata schimba tabelele.

`CREATE TABLE IF NOT EXISTS` nu poate adauga o coloana la o tabela veche. Fiecare modul isi
declara schema initiala (idempotenta) si o lista de migrari numerotate; versiunea aplicata
sta in tabela `schema_versiuni` (una pe modul, pentru ca events.db e impartit de jurnal,
marcaje si jobs). Migrarile ruleaza o singura data, in ordine, intr-o tranzactie fiecare.

    MIGRARI = {2: "ALTER TABLE events ADD COLUMN x TEXT", 3: functie(conn)}
    schema.aplica(conn, "jurnal", SCHEMA, MIGRARI)
"""
import logare

_TABELA = ("CREATE TABLE IF NOT EXISTS schema_versiuni "
           "(nume TEXT PRIMARY KEY, versiune INTEGER NOT NULL)")


def versiune(conn, nume):
    conn.execute(_TABELA)
    r = conn.execute("SELECT versiune FROM schema_versiuni WHERE nume = ?", (nume,)).fetchone()
    return r[0] if r else 0


def aplica(conn, nume, schema_sql, migrari=None):
    """Schema initiala (idempotenta), apoi migrarile cu numar mai mare decat versiunea aplicata.
    O baza noua primeste direct versiunea maxima (schema initiala e deja „la zi”)."""
    migrari = migrari or {}
    tinta = max([1, *migrari.keys()])
    conn.executescript(schema_sql)
    actuala = versiune(conn, nume)
    if actuala == 0:
        conn.execute("INSERT OR REPLACE INTO schema_versiuni (nume, versiune) VALUES (?, ?)",
                     (nume, tinta))
        return tinta
    for v in sorted(migrari):
        if v <= actuala:
            continue
        pas = migrari[v]
        marcheaza = ("INSERT OR REPLACE INTO schema_versiuni (nume, versiune) "
                     f"VALUES ('{nume}', {int(v)})")
        try:
            if callable(pas):
                conn.execute("BEGIN")
                pas(conn)
                conn.execute(marcheaza)
                conn.execute("COMMIT")
            else:
                # executescript comite singur ce e in asteptare, deci tranzactia e in script
                conn.executescript(f"BEGIN;\n{pas.strip().rstrip(';')};\n{marcheaza};\nCOMMIT;")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        logare.info(f"[schema] {nume}: migrat la versiunea {v}")
        actuala = v
    return actuala
