"""Cooldown-ul pe intrus: acelasi om re-declansand perimetrul nu e un intrus nou.

Masurat 11-21 sept: 144 din 220 de alerte "intrus persoana" veneau la <5 min
dupa precedenta, pe aceeasi camera — cineva lucrand prin curte, fragmentat in
alerte separate. Prima ramane instanta; repetitiile din fereastra tac.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import camctl  # noqa: E402


@pytest.fixture(autouse=True)
def curat(monkeypatch):
    monkeypatch.setattr(camctl, "_intrus_last", {})


def test_prima_alerta_trece_repetitia_tace():
    assert camctl._intrus_cooldown_ok(7, "person", now=1000.0) is True
    assert camctl._intrus_cooldown_ok(7, "person", now=1200.0) is False   # +3:20
    assert camctl._intrus_cooldown_ok(7, "person", now=1000.0 + 301) is True


def test_ferestrele_sunt_per_camera_si_eticheta():
    assert camctl._intrus_cooldown_ok(7, "person", now=1000.0) is True
    assert camctl._intrus_cooldown_ok(15, "person", now=1001.0) is True    # alta camera
    assert camctl._intrus_cooldown_ok(7, "car", now=1002.0) is True        # alta eticheta
    assert camctl._intrus_cooldown_ok(15, "person", now=1010.0) is False


def test_repetitia_nu_prelungeste_fereastra():
    # incercarile respinse NU muta ancora: dupa 5 min de la PRIMA, alerta revine
    camctl._intrus_cooldown_ok(7, "person", now=1000.0)
    camctl._intrus_cooldown_ok(7, "person", now=1290.0)                    # respinsa
    assert camctl._intrus_cooldown_ok(7, "person", now=1310.0) is True
