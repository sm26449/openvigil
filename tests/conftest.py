"""Fixture comun: jurnalul de evenimente (SQLite) merge intr-un director temporar.

Fara asta, orice test care emite un eveniment ar scrie in data/events.db-ul REAL — si,
rulat ca root, l-ar lasa pe root (serviciul ruleaza ca alt utilizator). Tot aici redirectam
events.jsonl, ca migrarea sa nu atinga niciodata fisierul adevarat din teste.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import jurnal  # noqa: E402


@pytest.fixture(autouse=True)
def _jurnal_in_tmp(tmp_path, monkeypatch):
    jurnal.reset()
    monkeypatch.setattr(jurnal, "DB_FILE", str(tmp_path / "events.db"))
    monkeypatch.setattr(jurnal, "JSONL_FILE", str(tmp_path / "events.jsonl"))
    yield
    jurnal.reset()
