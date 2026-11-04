"""Fixture comun.

Jurnalul de evenimente e pe PostgreSQL din 2.0.0: fara un server Postgres (CI il porneste,
vezi .github/workflows/ci.yml), testele se SAR — aplicatia oricum nu ruleaza fara baza. Cand
PG e disponibil, golim tabelul `events` inainte de fiecare test ca sa fie izolat (baza e
partajata intre teste). Auditul (JSONL) ramane pe fisier si merge intr-un director temporar,
ca testele sa nu atinga niciodata fisierul real. (Lantul de custodie e si el pe PostgreSQL din
Faza 3, cu propria fixtura in test_lant/test_arhiva.)
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import audit  # noqa: E402
import db  # noqa: E402
import jurnal  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    # auditul (JSONL) ramane pe fisier -> director temporar (lantul de custodie e pe PG, cu
    # propria fixtura in test_lant/test_arhiva)
    monkeypatch.setattr(audit, "FILE", str(tmp_path / "changes.jsonl"))
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
