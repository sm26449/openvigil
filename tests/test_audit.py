"""Jurnalul de modificari (app/audit.py): utilizatorul si IP-ul se completeaza singure
dintr-o cerere Flask, secretele nu intra, iar plasa de siguranta prinde modificarile
neauditate explicit."""
import json
import os
import sys

import pytest
from flask import Flask, g, jsonify

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import audit  # noqa: E402


@pytest.fixture(autouse=True)
def _audit_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "FILE", str(tmp_path / "changes.jsonl"))


def _app():
    app = Flask(__name__)
    app.secret_key = "x"

    @app.before_request
    def _s():
        from flask import session
        session["user"] = "ana"

    @app.route("/api/setari", methods=["POST"])
    def setari():
        return jsonify({"ok": True})

    @app.route("/api/explicit", methods=["POST"])
    def explicit():
        audit.log("setare salvata", "x", {"a": 1, "pass": "secret"})
        return jsonify({"ok": True})

    @app.route("/api/pica", methods=["POST"])
    def pica():
        return jsonify({"error": "nu"}), 400

    @app.after_request
    def _a(resp):
        audit.cerere_neauditata(resp)
        return resp
    return app


def test_in_afara_cererii_who_e_liniuta():
    audit.log("pornire", "sistem", {"telegram_token": "T", "x": 1})
    r = audit.tail(5)[0]
    assert r["who"] == "-" and "ip" not in r and r["detail"] == {"x": 1}


def test_in_cerere_utilizatorul_si_ipul_vin_singure():
    c = _app().test_client()
    c.post("/api/explicit", json={}, environ_base={"REMOTE_ADDR": "10.0.0.7"})
    rows = audit.tail(5)
    assert len(rows) == 1                               # explicit => plasa NU dubleaza
    assert rows[0]["who"] == "ana" and rows[0]["ip"] == "10.0.0.7"
    assert rows[0]["detail"] == {"a": 1}                # parola nu intra


def test_plasa_de_siguranta_prinde_modificarile_neauditate():
    c = _app().test_client()
    c.post("/api/setari", json={"telegram_chat": "123", "telegram_token": "SECRET",
                                "lista": [1, 2, 3], "lung": "x" * 200},
           environ_base={"REMOTE_ADDR": "10.0.0.8"})
    c.post("/api/pica", json={"a": 1})                  # 400 => nu se jurnalizeaza
    c.get("/api/setari")                                # GET => nu
    rows = audit.tail(5)
    assert len(rows) == 1
    r = rows[0]
    assert r["action"] == "cerere" and r["target"] == "POST /api/setari"
    assert r["who"] == "ana" and r["ip"] == "10.0.0.8"
    assert r["detail"]["telegram_chat"] == "123" and "telegram_token" not in r["detail"]
    assert r["detail"]["lista"] == "[1, 2, 3]" and len(r["detail"]["lung"]) == 78


def test_cautarea_vede_si_ipul_si_taierea():
    for i in range(5):
        audit.log(f"a{i}", who="u")
    assert [r["action"] for r in audit.tail(2)] == ["a4", "a3"]
    assert audit.tail(10, q="a2")[0]["action"] == "a2"


def test_secretele_noi_si_imbricate_nu_intra_in_jurnal(tmp_path):
    """audit S3: jetonul Telegram, cheia serviciului de alerte si a modelului de limbaj (si
    orice camp care arata a secret, la orice nivel) nu ajung in changes.jsonl."""
    secrete = {"jeton": "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "alert_service_key": "cheie-alerte-1",
               "llm_key": "sk-test-2", "alert_service_url": "http://127.0.0.1:5090/v1/fire",
               "imbricat": {"telegram": {"bot_token": "t-3"}, "lista": [{"api_key": "k-4"}]}}
    audit.log("setari salvate", "x", secrete)
    app = _app()
    app.test_client().post("/api/setari", json=secrete)
    text = open(audit.FILE).read()
    for v in ("AAAAAAAAAA", "cheie-alerte-1", "sk-test-2", "t-3", "k-4"):
        assert v not in text, v
    assert "http://127.0.0.1:5090/v1/fire" in text                 # ce nu e secret ramane vizibil
    assert '"jeton"' not in text and "llm_key" not in text
    assert oct(os.stat(audit.FILE).st_mode)[-3:] == "600"
