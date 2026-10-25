"""Sesiuni revocabile si 2FA pe conturi (app/users.py): versiunea sesiunii creste la
schimbarea parolei si la revocare; secretul TOTP, contorul anti-replay si codurile de
recuperare (de unica folosinta) se tin in cont."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import users  # noqa: E402


@pytest.fixture(autouse=True)
def _users_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "PATH", str(tmp_path / "users.json"))
    users.upsert("admin", password="parola1", role="admin")


def test_versiunea_sesiunii_creste_la_parola_noua_si_la_revocare():
    assert users.sess_ver(users.get("admin")) == 0
    users.upsert("admin", role="admin")                    # fara parola: neschimbat
    assert users.sess_ver(users.get("admin")) == 0
    users.upsert("admin", password="parola2")
    assert users.sess_ver(users.get("admin")) == 1
    assert users.revoke("admin") == 2
    assert users.sess_ver(users.get("admin")) == 2
    with pytest.raises(ValueError):
        users.revoke("nimeni")


def test_cont_nou_porneste_de_la_zero():
    users.upsert("ana", password="parola1", role="viewer")
    assert users.sess_ver(users.get("ana")) == 0            # parola initiala nu e „schimbare"
    assert users.public(users.get("ana"))["totp"] is False


def test_totp_activare_replay_si_recuperare():
    users.set_totp("admin", "SECRET", ["cod-unu", "cod-doi"])
    u = users.get("admin")
    assert u["totp"]["secret"] == "SECRET" and u["totp"]["ultimul"] == 0
    assert all(h.startswith("scrypt:") for h in u["totp"]["recuperare"])   # hash-uite
    assert users.public(u)["totp"] is True and "totp" not in users.public(u).get("hash", "")
    assert users.totp_folosit("admin", 100) is True
    assert users.totp_folosit("admin", 100) is False        # acelasi pas: replay
    assert users.totp_folosit("admin", 99) is False
    assert users.totp_folosit("admin", 101) is True
    assert users.recuperare_folosita("admin", "gresit") is False
    assert users.recuperare_folosita("admin", "COD-UNU") is True   # case-insensitive
    assert users.recuperare_folosita("admin", "cod-unu") is False  # consumat
    assert len(users.get("admin")["totp"]["recuperare"]) == 1
    assert users.clear_totp("admin") is True
    assert users.clear_totp("admin") is False
    assert users.totp_folosit("admin", 200) is False
    with pytest.raises(ValueError):
        users.set_totp("nimeni", "S", [])
