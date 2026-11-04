"""Fixture comun.

Jurnalul de evenimente e pe PostgreSQL din 2.0.0: fara un server Postgres (CI il porneste,
vezi .github/workflows/ci.yml), testele se SAR — aplicatia oricum nu ruleaza fara baza. Cand
PG e disponibil, golim tabelul `events` inainte de fiecare test ca sa fie izolat (baza e
partajata intre teste). Auditul (JSONL) si lantul de custodie (SQLite, segmente.db) raman pe
fisier si merg intr-un director temporar, ca testele sa nu atinga niciodata fisierele reale.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import audit  # noqa: E402
import db  # noqa: E402
import jurnal  # noqa: E402
import lant  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    # audit (JSONL) + lantul de custodie (SQLite) raman pe fisier -> director temporar
    monkeypatch.setattr(audit, "FILE", str(tmp_path / "changes.jsonl"))
    monkeypatch.setattr(lant, "DB_FILE", str(tmp_path / "segmente.db"))
    # jurnalul e pe PostgreSQL: sare daca nu e disponibil, altfel goleste `events`
    try:
        db.ping()
    except Exception as e:
        pytest.skip(f"PostgreSQL indisponibil: {e}")
    jurnal.reset()
    jurnal.deschide()
    with db.conn() as c:
        c.execute("TRUNCATE events")
    yield
    jurnal.reset()
