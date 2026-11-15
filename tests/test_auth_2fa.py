"""2FA prin HTTP (app/routes_auth.py): activarea cere parola si nu inlocuieste un factor activ
(audit S4); biletul dintre parola si cod nu supravietuieste unei revocari (audit S9)."""
import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import routes_auth  # noqa: E402
import totp  # noqa: E402
import users  # noqa: E402

PAROLA = "parola-buna-123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(users, "PATH", str(tmp_path / "users.json"))
    users.upsert("sef", password="admin-parola-123", role="admin")
    users.upsert("ana", password=PAROLA, role="viewer")
    monkeypatch.setattr(routes_auth, "current_user", lambda: users.get("ana"))
    routes_auth._login_fail.clear()
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(routes_auth.bp)
    return app.test_client()


def _activeaza(c, parola=PAROLA):
    d = c.post("/api/me/2fa/pregateste").get_json()
    return c.post("/api/me/2fa/activeaza", json={"cod": totp.cod(d["secret"]), "parola": parola})


def test_activarea_cere_parola(client):
    r = _activeaza(client, parola="gresita")
    assert r.status_code == 400 and not users.get("ana").get("totp")
    r = _activeaza(client)
    assert r.status_code == 200 and users.get("ana").get("totp") and len(r.get_json()["recuperare"]) == 8


def test_un_factor_activ_nu_se_inlocuieste_cu_sesiunea(client):
    assert _activeaza(client).status_code == 200
    vechi = users.get("ana")["totp"]["secret"]
    r = client.post("/api/me/2fa/pregateste")
    assert r.status_code == 400
    assert users.get("ana")["totp"]["secret"] == vechi


def test_secretul_in_pregatire_e_legat_de_sesiune(client, tmp_path):
    d = client.post("/api/me/2fa/pregateste").get_json()
    alt = client.application.test_client()                     # alta sesiune, acelasi cont
    r = alt.post("/api/me/2fa/activeaza", json={"cod": totp.cod(d["secret"]), "parola": PAROLA})
    assert r.status_code == 400 and not users.get("ana").get("totp")


def test_activarea_deconecteaza_celelalte_sesiuni(client):
    v0 = users.sess_ver(users.get("ana"))
    assert _activeaza(client).status_code == 200
    assert users.sess_ver(users.get("ana")) != v0


def test_biletul_2fa_cade_la_revocare(client, monkeypatch):
    assert _activeaza(client).status_code == 200
    secret = users.get("ana")["totp"]["secret"]
    monkeypatch.setattr(routes_auth, "current_user", lambda: None)   # de aici: nelogat
    r = client.post("/login", data={"user": "ana", "password": PAROLA})
    assert r.status_code == 200                                  # pagina codului
    users.revoke("ana")                                          # adminul revoca intre timp
    r = client.post("/login/2fa", data={"cod": totp.cod(secret)})
    assert r.status_code == 302 and r.headers["Location"].endswith("/login")
    with client.session_transaction() as s:
        assert "user" not in s
