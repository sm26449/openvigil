"""Versiunea schemei SQLite (app/schema.py): baza noua = versiunea maxima; migrarile ruleaza
o singura data, in ordine, si se opresc la prima care esueaza (tranzactie)."""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import schema  # noqa: E402

SCHEMA = "CREATE TABLE IF NOT EXISTS t (id INTEGER PRIMARY KEY, a TEXT);"


def _conn():
    return sqlite3.connect(":memory:", isolation_level=None)


def test_baza_noua_primeste_versiunea_maxima():
    c = _conn()
    assert schema.aplica(c, "x", SCHEMA, {2: "ALTER TABLE t ADD COLUMN b TEXT"}) == 2
    assert schema.versiune(c, "x") == 2
    # schema initiala nu are coloana b; o baza NOUA trebuie sa fie deja „la zi” — deci
    # schema initiala e cea completa, migrarea e pentru bazele vechi (conventie documentata)
    assert schema.aplica(c, "x", SCHEMA, {2: "ALTER TABLE t ADD COLUMN b TEXT"}) == 2   # idempotent


def test_migrarile_ruleaza_o_data_si_in_ordine():
    c = _conn()
    schema.aplica(c, "x", SCHEMA)                       # versiunea 1, ca o instalare veche
    pasi = []
    mig = {3: lambda conn: pasi.append(3), 2: "ALTER TABLE t ADD COLUMN b TEXT"}
    assert schema.aplica(c, "x", SCHEMA, mig) == 3
    assert pasi == [3]
    assert [r[1] for r in c.execute("PRAGMA table_info(t)")] == ["id", "a", "b"]
    assert schema.aplica(c, "x", SCHEMA, mig) == 3 and pasi == [3]   # a doua oara: nimic


def test_migrare_esuata_nu_avanseaza_versiunea():
    c = _conn()
    schema.aplica(c, "x", SCHEMA)
    with pytest.raises(sqlite3.OperationalError):
        schema.aplica(c, "x", SCHEMA, {2: "ALTER TABLE lipsa ADD COLUMN b TEXT"})
    assert schema.versiune(c, "x") == 1


def test_module_separate_in_aceeasi_baza():
    c = _conn()
    schema.aplica(c, "jurnal", SCHEMA, {2: "CREATE TABLE IF NOT EXISTS j2 (x)"})
    schema.aplica(c, "marcaje", "CREATE TABLE IF NOT EXISTS m (x);")
    assert schema.versiune(c, "jurnal") == 2 and schema.versiune(c, "marcaje") == 1
