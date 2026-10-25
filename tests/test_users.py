"""Teste pentru app/users.py — conturi, roluri si restrictia pe camere.

Ruleaza cu:  venv/bin/python -m pytest tests/ -q
Fisierul de conturi e redirectionat spre un director temporar, deci testele nu ating
data/users.json al sistemului.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import users  # noqa: E402


@pytest.fixture(autouse=True)
def _users_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "PATH", str(tmp_path / "users.json"))


def test_fara_fisier_inseamna_niciun_cont():
    assert users.load() == []
    assert users.get("admin") is None


def test_creare_si_verificare_parola():
    users.upsert("admin", password="parola1", role="admin")
    assert users.verify("admin", "parola1")["role"] == "admin"
    assert users.verify("admin", "gresita") is None
    assert users.verify("inexistent", "parola1") is None


def test_parola_scurta_e_refuzata():
    with pytest.raises(ValueError):
        users.upsert("admin", password="scurt", role="admin")


def test_nume_invalid_e_refuzat():
    with pytest.raises(ValueError):
        users.upsert("a b!", password="parola1", role="admin")


def test_rol_necunoscut_e_refuzat():
    with pytest.raises(ValueError):
        users.upsert("admin", password="parola1", role="root")


def test_rol_operator_e_valid_si_poate_fi_restrans():
    users.upsert("admin", password="parola1", role="admin")
    users.upsert("paznic", password="parola2", role="operator", cameras=[101])
    u = users.get("paznic")
    assert u["role"] == "operator"
    assert users.allowed_ids(u, []) == {101}   # restrictia pe camere se aplica si lui


def test_modificare_fara_parola_pastreaza_hash():
    users.upsert("admin", password="parola1", role="admin")
    vechi = users.get("admin")["hash"]
    users.upsert("admin", role="admin")   # fara parola = neschimbata
    assert users.get("admin")["hash"] == vechi
    assert users.verify("admin", "parola1")


def test_nu_ramane_sistemul_fara_admin():
    users.upsert("admin", password="parola1", role="admin")
    with pytest.raises(ValueError):
        users.upsert("admin", role="viewer")   # singurul admin retrogradat -> refuz
    users.upsert("ana", password="parola2", role="viewer")
    with pytest.raises(ValueError):
        users.delete("admin")                  # ultimul admin sters -> refuz
    users.upsert("admin2", password="parola3", role="admin")
    users.delete("admin")                      # acum se poate: mai ramane admin2


def test_stergere_utilizator_inexistent():
    users.upsert("admin", password="parola1", role="admin")
    with pytest.raises(ValueError):
        users.delete("nimeni")


CAMS = [{"id": 101, "group": "Curte"}, {"id": 102, "group": "Curte"},
        {"id": 103, "group": "Poarta"}, {"id": 104, "group": None}]


def test_allowed_ids_admin_vede_tot():
    users.upsert("admin", password="parola1", role="admin")
    assert users.allowed_ids(users.get("admin"), CAMS) is None


def test_allowed_ids_viewer_nerestrans_vede_tot():
    users.upsert("admin", password="parola1", role="admin")
    users.upsert("ana", password="parola2", role="viewer")
    assert users.allowed_ids(users.get("ana"), CAMS) is None
    # liste goale = tot nerestrans (formularul trimite mereu liste)
    users.upsert("ana", cameras=[], groups=[])
    assert users.allowed_ids(users.get("ana"), CAMS) is None


def test_allowed_ids_combina_grupuri_si_camere():
    users.upsert("admin", password="parola1", role="admin")
    users.upsert("ana", password="parola2", role="viewer",
                 groups=["Curte"], cameras=[104])
    assert users.allowed_ids(users.get("ana"), CAMS) == {101, 102, 104}


def test_grup_lipsa_devine_altele():
    users.upsert("admin", password="parola1", role="admin")
    users.upsert("ana", password="parola2", role="viewer", groups=["Altele"])
    assert users.allowed_ids(users.get("ana"), CAMS) == {104}


def test_fisierul_se_scrie_cu_permisiuni_stranse():
    users.upsert("admin", password="parola1", role="admin")
    assert oct(os.stat(users.PATH).st_mode & 0o777) == "0o600"
