"""Teste pentru app/backup.py — completitudine si curatarea secretelor.

Auditul din 2026-08-14 a gasit doua probleme reale: users/rules/plates lipseau din
backup, iar varianta "fara parole" scurgea api_token, webhook_secret si mqtt_pass.
Testele de aici pazesc amandoua.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import backup  # noqa: E402


@pytest.fixture
def _config_in_tmp(tmp_path, monkeypatch):
    files = {
        "cameras": tmp_path / "cameras.json",
        "settings": tmp_path / "settings.json",
        "detect": tmp_path / "detect.json",
        "users": tmp_path / "users.json",
        "rules": tmp_path / "rules.json",
        "plates_known": tmp_path / "plates" / "known.json",
    }
    monkeypatch.setattr(backup, "FILES", {k: str(v) for k, v in files.items()})
    (tmp_path / "plates").mkdir()
    files["cameras"].write_text(json.dumps([{"id": 101, "ip": "1.2.3.4",
                                             "user": "admin", "pass": "secret"}]))
    files["settings"].write_text(json.dumps({
        "retention_days": 7, "web_password": "hash", "telegram_token": "tg",
        "email_pass": "em", "api_token": "api", "webhook_secret": "wh",
        "mqtt_pass": "mq"}))
    files["detect"].write_text(json.dumps({"101": {"on": True}}))
    files["users"].write_text(json.dumps([{"user": "admin", "hash": "h", "role": "admin"}]))
    files["rules"].write_text(json.dumps([{"id": "r1", "nume": "x"}]))
    files["plates_known"].write_text(json.dumps({"B123XYZ": "duba"}))
    return files


def test_exportul_include_toate_sectiunile(_config_in_tmp):
    out = backup.export_config()
    assert set(out["date"]) == {"cameras", "settings", "detect", "users",
                                "rules", "plates_known"}
    assert out["date"]["users"][0]["hash"] == "h"
    assert out["date"]["plates_known"] == {"B123XYZ": "duba"}


def test_varianta_fara_parole_nu_scurge_niciun_secret(_config_in_tmp):
    out = backup.export_config(include_secrets=False)
    text = json.dumps(out)
    for secret in ("secret", '"hash"', '"tg"', '"em"', '"api"', '"wh"', '"mq"'):
        assert secret not in text, f"secretul {secret} a scapat in exportul fara parole"
    assert out["date"]["settings"]["retention_days"] == 7   # ce nu e secret ramane


def test_refacerea_scrie_si_sectiunile_noi(_config_in_tmp):
    out = backup.export_config()
    for p in backup.FILES.values():
        os.remove(p)
    restored = backup.import_config(out)
    assert set(restored) == set(backup.FILES)
    assert json.load(open(backup.FILES["rules"]))[0]["id"] == "r1"


def test_refacerea_refuza_utilizatori_fara_hash(_config_in_tmp):
    out = backup.export_config(include_secrets=False)   # utilizatori fara hash
    restored = backup.import_config(out)
    assert "users" not in restored   # conturile nu se pot reface dintr-un export sterilizat
    assert json.load(open(backup.FILES["users"]))[0]["hash"] == "h"   # cele vechi raman
