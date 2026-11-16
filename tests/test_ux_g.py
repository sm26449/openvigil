"""UX-G (audit UX 2026-11-14): „Cât ai lipsit”, asistentul Telegram si triajul pe grupuri prin HTTP."""
import os
import sys
import time

import pytest
from flask import Flask

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import absenta  # noqa: E402
import incidents  # noqa: E402
import rules  # noqa: E402


def _ev(i, t, tip="episod", cam=6, sev="critic", **kw):
    return {"id": f"e{i}", "t": t, "tip": tip, "cam": cam, "sev": sev, **kw}


def test_rezumat_absenta_grupeaza_si_numara(monkeypatch):
    acum = time.mktime(time.strptime("2026-11-14 08:00:00", "%Y-%m-%d %H:%M:%S"))
    inc = [_ev(1, "2026-11-14 01:10:00"), _ev(2, "2026-11-14 01:00:00"), _ev(3, "2026-11-14 00:50:00"),
           _ev(4, "2026-11-14 03:00:00", tip="intrus", cam=7, eticheta="person")]
    pers = [{"t": "2026-11-14 02:00:00", "tip": "persoana", "cam": 1, "nume": "ana"}] * 2
    jos = [{"t": "2026-11-14 04:00:00", "tip": "camera_offline", "cam": 9}]

    def journal(limit=200, tip=None, since=None, cams=None, min_sev=None, **kw):
        if tip == "persoana":
            return [dict(x) for x in pers]
        if tip == "camera_offline":
            return [dict(x) for x in jos]
        if tip:
            return []
        return [dict(x) for x in inc]
    monkeypatch.setattr(rules, "journal", journal)
    monkeypatch.setattr(rules, "event_severity", lambda e: e["sev"])
    monkeypatch.setattr(incidents, "annotate", lambda evs: [e.setdefault("stare", "nou") for e in evs])
    monkeypatch.setattr(incidents, "adauga_poze", lambda evs: evs)
    r = absenta.rezumat(acum - 10 * 3600, acum=acum)
    assert r["incidente"] == 4 and r["critice"] == 4 and r["netratate"] == 4
    assert [(g["tip"], g["n"]) for g in r["grupuri"]] == [("episod", 3), ("intrus", 1)]
    assert r["persoane"] == [{"nume": "ana", "n": 2}]
    assert r["camere_cazute"] == [9]


def test_rezumat_absenta_cel_mult_7_zile(monkeypatch):
    vazut = {}
    monkeypatch.setattr(rules, "journal", lambda **kw: vazut.setdefault("since", kw.get("since")) and [])
    monkeypatch.setattr(incidents, "annotate", lambda evs: None)
    monkeypatch.setattr(incidents, "adauga_poze", lambda evs: evs)
    acum = 2_000_000_000
    absenta.rezumat(0, acum=acum)
    assert vazut["since"] == acum - 7 * 86400


@pytest.fixture
def sistem(monkeypatch):
    import routes_system as rs
    app = Flask(__name__)
    app.register_blueprint(rs.bp)
    salvat, trimis = {}, []
    monkeypatch.setattr(rs.settings, "update", lambda d: salvat.update(d))
    monkeypatch.setattr(rs.notify, "test_channel", lambda ch: trimis.append(ch))
    monkeypatch.setattr(rs.audit, "log", lambda *a, **k: None)
    return app.test_client(), rs, salvat, trimis


JETON = "123456789:AAAbbbCCCdddEEEfffGGGhhhIIIjjjKKK"


def test_asistent_telegram_jeton_invalid_nu_pleaca_spre_telegram(sistem, monkeypatch):
    c, rs, _, _ = sistem
    monkeypatch.setattr(rs, "_tg", lambda *a, **k: pytest.fail("nu trebuia sa cheme Telegram"))
    assert c.post("/api/telegram/asistent/verifica", json={"jeton": "../etc/passwd"}).status_code == 400


def test_asistent_telegram_gaseste_chatul_si_salveaza(sistem, monkeypatch):
    c, rs, salvat, trimis = sistem
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "activ", lambda: False)

    def tg(jeton, metoda, **p):
        if metoda == "getMe":
            return {"ok": True, "result": {"first_name": "Casa", "username": "casa_bot"}}
        return {"ok": True, "result": [{"update_id": 1, "message": {"date": 5, "chat": {"id": 4242, "type": "private", "first_name": "Ion"}}}]}
    monkeypatch.setattr(rs, "_tg", tg)
    r = c.post("/api/telegram/asistent/verifica", json={"jeton": JETON}).json
    assert r["utilizator"] == "casa_bot"
    chats = c.post("/api/telegram/asistent/chat", json={"jeton": JETON}).json["chats"]
    assert chats == [{"id": "4242", "tip": "private", "t": 5, "nume": "Ion"}]
    r = c.post("/api/telegram/asistent/salveaza", json={"jeton": JETON, "chat": "4242"}).json
    assert r["ok"] and r["proba"] and salvat == {"telegram_token": JETON, "telegram_chat": "4242"} and trimis == ["telegram"]


def test_asistent_telegram_cand_botul_asculta_deja(sistem, monkeypatch):
    """Bucla botului consuma getUpdates: chat-urile vin din ce a vazut ea, nu din Telegram."""
    c, rs, _, _ = sistem
    import telegram_bot
    monkeypatch.setattr(telegram_bot, "activ", lambda: True)
    monkeypatch.setattr(rs.settings, "get", lambda k: JETON if k == "telegram_token" else None)
    monkeypatch.setattr(telegram_bot, "vazute", {"7": {"id": "7", "tip": "group", "nume": "Familie", "t": 9}})
    monkeypatch.setattr(rs, "_tg", lambda *a, **k: pytest.fail("getUpdates s-ar bate cu bucla botului"))
    assert c.post("/api/telegram/asistent/chat", json={"jeton": JETON}).json["chats"][0]["nume"] == "Familie"


def test_stare_bloc_doar_incidentele_vizibile(monkeypatch):
    import routes_incidents as ri
    app = Flask(__name__)
    app.register_blueprint(ri.bp)
    monkeypatch.setattr(ri, "_visible_ids", lambda: {6})
    monkeypatch.setattr(ri.incidents_mod, "feed", lambda limit=None, visible=None: [{"id": "a", "cam": 6}])
    puse = {}
    monkeypatch.setattr(ri.incidents_mod, "set_state_bloc", lambda ids, stare, who=None: puse.update(ids=ids) or len(ids))
    monkeypatch.setattr(ri, "current_user", lambda: {"user": "op"})
    monkeypatch.setattr(ri.audit, "log", lambda *a, **k: None)
    r = app.test_client().post("/api/incidents/stare-bloc", json={"ids": ["a", "b"], "stare": "fals"})
    assert r.status_code == 200 and r.json["n"] == 1 and r.json["ignorate"] == 1 and puse["ids"] == ["a"]



def test_rezumatul_absentei_trece_de_garda_pentru_operator_si_viewer():
    """audit F1: interfata il cere si pentru operator; garda il refuza (403) daca lipseste din
    VIEWER_GET. Ruta filtreaza singura pe camerele contului (visible=_visible_ids())."""
    import server
    assert any("/api/rezumat/absenta" == w or "/api/rezumat/absenta".startswith(w) for w in server.VIEWER_GET)



def test_rezumatul_include_camera_cazuta_inainte_de_vizita(monkeypatch):
    """audit F5: camera cazuta inainte de ultima vizita (fara tranzitie in interval) si inca jos
    apare; cea revenita intre timp nu; camerele nepermise contului nu."""
    import notify
    monkeypatch.setattr(rules, "journal", lambda **kw: [])
    monkeypatch.setattr(incidents, "annotate", lambda evs: None)
    monkeypatch.setattr(incidents, "adauga_poze", lambda evs: evs)
    monkeypatch.setattr(notify, "_states", {3: False, 4: True, 8: False, "nvr": False})
    acum = 2_000_000_000
    assert absenta.rezumat(acum - 3600, acum=acum)["camere_cazute"] == [3, 8]
    assert absenta.rezumat(acum - 3600, visible={3, 4}, acum=acum)["camere_cazute"] == [3]
