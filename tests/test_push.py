"""Web Push (app/push.py): abonamente in SQLite, filtrarea pe nivel si pe camerele permise,
stergerea abonamentelor moarte (404/410), mesajul construit dintr-un eveniment."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import push  # noqa: E402
import users  # noqa: E402

SUB = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "P", "auth": "A"}}
SUB2 = {"endpoint": "https://push.example/def", "keys": {"p256dh": "P", "auth": "A"}}


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    push.reset()
    monkeypatch.setattr(push, "VAPID_FILE", str(tmp_path / "vapid.json"))
    monkeypatch.setattr(users, "PATH", str(tmp_path / "users.json"))
    users.upsert("admin", password="parola1", role="admin")
    users.upsert("paznic", password="parola1", role="operator", cameras=[5])
    push.camere_fn = lambda: [{"id": 5, "name": "Poarta"}, {"id": 9, "name": "Spate"}]
    trimise = []
    monkeypatch.setattr(push, "_webpush", lambda **kw: trimise.append(kw))
    yield trimise
    push.reset()


def test_cheia_vapid_se_genereaza_o_data(tmp_path):
    k = push.cheie_publica()
    assert k and len(k) > 80 and os.path.exists(tmp_path / "vapid.json")
    assert oct(os.stat(tmp_path / "vapid.json").st_mode)[-3:] == "600"
    push.reset()
    assert push.cheie_publica() == k


def test_abonare_validare_si_dezabonare():
    with pytest.raises(ValueError):
        push.aboneaza("admin", {"endpoint": "http://nesigur"})
    with pytest.raises(ValueError):
        push.aboneaza("admin", {"endpoint": "https://x", "keys": {}})
    a = push.aboneaza("admin", SUB, "atentie", "Chrome")
    assert a["user"] == "admin" and a["nivel"] == "atentie" and a["agent"] == "Chrome"
    # acelasi endpoint, alt cont: trece la contul nou (telefonul a schimbat utilizatorul)
    push.aboneaza("paznic", SUB, "gresit")
    assert [x["user"] for x in push.lista()] == ["paznic"]
    assert push.lista()[0]["nivel"] == "critic"
    assert push.dezaboneaza("admin", SUB["endpoint"]) is False     # nu e al lui
    assert push.dezaboneaza("admin", SUB["endpoint"], admin=True) is True
    assert push.lista() == []


def test_filtrare_pe_nivel_si_camere(_izolare):
    push.aboneaza("admin", SUB, "critic")
    push.aboneaza("paznic", SUB2, "atentie")
    # atentie pe camera 9: adminul vrea doar critic, paznicul nu vede camera 9
    assert push.trimite({"x": 1}, sev="atentie", cam_id=9, sync=True) == 0
    # atentie pe camera 5: doar paznicul
    assert push.trimite({"x": 1}, sev="atentie", cam_id=5, sync=True) == 1
    assert _izolare[-1]["subscription_info"]["endpoint"] == SUB2["endpoint"]
    # critic pe camera 9: doar adminul (paznicul n-o vede)
    assert push.trimite({"x": 1}, sev="critic", cam_id=9, sync=True) == 1
    assert _izolare[-1]["subscription_info"]["endpoint"] == SUB["endpoint"]
    # critic fara camera (sistem): amandoi
    assert push.trimite({"x": 1}, sev="critic", sync=True) == 2
    # mesaj de proba: doar dispozitivele contului
    assert push.test("paznic") == 1
    assert "mailto:" in _izolare[-1]["vapid_claims"]["sub"]


def test_eveniment_construieste_notificarea(_izolare):
    push.aboneaza("admin", SUB, "critic")
    e = {"id": "ev1", "t": "2026-10-23 14:30:05", "tip": "intrus", "sev": "critic", "cam": 5,
         "eticheta": "person"}
    assert push.eveniment(e, sync=True) == 1
    import json
    d = json.loads(_izolare[-1]["data"])
    assert d["titlu"] == "🚨 Poarta" and d["corp"] == "INTRUS confirmat: person"
    assert d["url"] == "/#pb/5/2026-10-23/52195" and d["tag"] == "ev1"
    assert push.eveniment({"id": "x", "sev": "info", "tip": "detectie"}) == 0
    # fara camera: legatura duce la panoul de incidente
    push.eveniment({"id": "y", "sev": "critic", "tip": "zgomot", "t": "2026-10-23 10:00:00"}, sync=True)
    assert json.loads(_izolare[-1]["data"])["url"] == "/#ev/inc"


def test_abonamentul_mort_se_sterge_si_erorile_se_numara(monkeypatch):
    push.aboneaza("admin", SUB, "critic")
    push.aboneaza("admin", SUB2, "critic")

    class R:
        status_code = 410

    class Mort(Exception):
        response = R()

    def fals(**kw):
        if kw["subscription_info"]["endpoint"] == SUB["endpoint"]:
            raise Mort("gone")
        raise RuntimeError("retea")
    monkeypatch.setattr(push, "_webpush", fals)
    push.trimite({"x": 1}, user="admin", sync=True)
    rest = push.lista("admin")
    assert [x["endpoint"] for x in rest] == [SUB2["endpoint"]]
    assert rest[0]["erori"] == 1
