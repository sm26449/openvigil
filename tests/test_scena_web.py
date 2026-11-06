"""Partea de web a „Invata scena” (app/scena_web.py): feedback-ul in jsonl, filtrul si
cooldown-ul candidatilor trimisi pe Telegram, butoanele (callback_data) si ce scriu ele,
analiza adanca ceruta din buton. Telegram si nvr-ai sunt inlocuite cu functii-martor."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import scena_web  # noqa: E402
import settings  # noqa: E402
import telegram_bot  # noqa: E402

CFG = {"telegram_token": "t", "telegram_chat": "42", "scena_telegram": True,
       "scena_telegram_cooldown": 300, "scena_telegram_cooldown_necunoscut": 900,
       "scena_vocabular": ["om", "pisică", "tractor"]}


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(scena_web, "SCENA_DIR", str(tmp_path))
    monkeypatch.setattr(scena_web, "FEEDBACK", str(tmp_path / "feedback.jsonl"))
    monkeypatch.setattr(settings, "load", lambda: dict(CFG))
    monkeypatch.setattr(settings, "get", lambda k, d=None: CFG.get(k, d))
    monkeypatch.setattr(scena_web, "_nume_cam", lambda c: f"cam{c}")
    import vreme
    monkeypatch.setattr(vreme, "acum", lambda: {})          # fara vremea reala a instalatiei
    import rules
    monkeypatch.setattr(rules, "_is_night", lambda *a: False)   # ziua, daca testul nu zice altfel
    scena_web._ultim.clear()
    scena_web._analize.clear()
    apeluri = []
    monkeypatch.setattr(scena_web, "_tg", lambda cfg, metoda, fisiere=None, **d: apeluri.append((metoda, d, fisiere)) or {"message_id": 7})
    yield apeluri


def test_feedback_scris_si_citit():
    r = scena_web.feedback_adauga(14, "abc", "gresit", eticheta="pisică", poza="x.jpg", cine="stefan")
    assert r["cam"] == 14 and r["eticheta"] == "pisică"
    scena_web.feedback_adauga(14, "abc", "ok", cine="maria")        # ultimul cuvant conteaza
    scena_web.feedback_adauga(6, "zzz", "necunoscut")
    fb = scena_web.feedback_citeste(14)
    assert set(fb) == {"abc"} and fb["abc"]["verdict"] == "ok"
    assert len(scena_web.feedback_citeste()) == 2
    with pytest.raises(ValueError):
        scena_web.feedback_adauga(14, "abc", "poate")


def test_de_trimis_filtru_si_cooldown():
    # eticheta numita (nu „necunoscut"): cooldown normal de 300 s pe camera
    e = {"cam": 14, "id": "a1", "poza": "p.jpg", "eticheta": "mașină", "sursa": "yolo-world", "incredere": 0.7}
    assert scena_web.de_trimis(e, now=1000)
    assert not scena_web.de_trimis({**e, "id": "a2"}, now=1100)       # cooldown pe camera (100<300)
    assert scena_web.de_trimis({**e, "cam": 6}, now=1100)             # alta camera, liber
    assert scena_web.de_trimis({**e, "id": "a3"}, now=1400)           # 400>300
    assert not scena_web.de_trimis({**e, "eticheta": "ploaie", "cam": 101}, now=1)
    assert not scena_web.de_trimis({**e, "poza": None, "cam": 101}, now=1)
    CFG["scena_telegram"] = False
    try:
        assert not scena_web.de_trimis({**e, "cam": 13}, now=1)
    finally:
        CFG["scena_telegram"] = True


def test_cooldown_mai_lung_pentru_necunoscut():
    """„necunoscut" (cerere de verdict) pleaca pe cooldown-ul lui, mai lung (900 s), nu pe cel normal."""
    e = {"cam": 14, "id": "n1", "poza": "p.jpg", "eticheta": "necunoscut"}
    assert scena_web.de_trimis(e, now=1000)                           # prima cerere pleaca
    assert not scena_web.de_trimis({**e, "id": "n2"}, now=1400)       # 400 < 900 -> sarit (era <300 inainte)
    assert scena_web.de_trimis({**e, "id": "n3"}, now=1901)           # 901 > 900 -> pleaca din nou


def test_noaptea_doar_etichete_sigure(monkeypatch):
    import rules
    monkeypatch.setattr(rules, "_is_night", lambda *a: True)
    e = {"cam": 14, "id": "n1", "poza": "p.jpg", "eticheta": "necunoscut"}
    assert not scena_web.de_trimis(e, now=1000)                                                   # granulatie IR
    assert not scena_web.de_trimis({**e, "eticheta": "pisică", "sursa": "vocabular", "incredere": 0.9}, now=1000)
    assert not scena_web.de_trimis({**e, "eticheta": "person", "sursa": "yolo-nesigur", "incredere": 0.45}, now=1000)
    assert not scena_web.de_trimis({**e, "eticheta": "pisică", "sursa": "yolo-world", "incredere": 0.42}, now=1000)
    assert scena_web.de_trimis({**e, "eticheta": "mașină", "sursa": "yolo-world", "incredere": 0.6}, now=1000)
    monkeypatch.setattr(rules, "_is_night", lambda *a: False)
    assert scena_web.de_trimis({**e, "cam": 6, "eticheta": "pisică", "sursa": "vocabular", "incredere": 0.05}, now=1000)


def test_prag_de_noapte_global_si_per_camera(monkeypatch):
    import detect
    import rules
    monkeypatch.setitem(CFG, "scena_telegram_noapte", 0.6)
    monkeypatch.setattr(detect, "cam_cfg", lambda c: {"scena_noapte": 0.8} if c == 6 else {})
    assert scena_web.prag_noapte(6) == 0.8 and scena_web.prag_noapte(14) == 0.6
    monkeypatch.setattr(rules, "_is_night", lambda *a: True)
    e = {"cam": 6, "id": "n1", "poza": "p.jpg", "eticheta": "pisică", "sursa": "yolo-world", "incredere": 0.75}
    assert not scena_web.de_trimis(e, now=1000)                                   # sub 0,8 pe cam 6
    assert scena_web.de_trimis({**e, "cam": 14}, now=1000)                          # peste 0,6 global
    monkeypatch.delitem(CFG, "scena_telegram_noapte")
    assert scena_web.prag_noapte(14) == scena_web.NOAPTE_INCREDERE


def test_verdictul_invata_camera(monkeypatch):
    """Orice verdict cu nume (fundal sau obiect) pleaca la nvr-ai ca exemplu; „?” si ✓ fara nume
    propus scot exemplul."""
    import ai_client
    apeluri = []
    monkeypatch.setattr(ai_client, "remote", lambda: True)
    monkeypatch.setattr(ai_client, "scena_fundal", lambda cam, poza, nume: apeluri.append((cam, poza, nume)))
    monkeypatch.setattr(ai_client, "semantic_scena", lambda *a: None)
    scena_web.feedback_adauga(14, "f1", "gresit", eticheta="nimic", poza="20261101-091845-7.jpg")
    scena_web.feedback_adauga(14, "f2", "gresit", eticheta="pisică", poza="20261101-091846-8.jpg")
    scena_web.feedback_adauga(14, "f3", "ok", poza="20261101-091847-9.jpg")
    scena_web.feedback_adauga(14, "f4", "ok", eticheta="găină", poza="20261101-091848-9.jpg")
    scena_web.feedback_adauga(14, "f4", "necunoscut", poza="20261101-091848-9.jpg")
    assert apeluri == [(14, "20261101-091845-7.jpg", "nimic"), (14, "20261101-091846-8.jpg", "pisică"),
                       (14, "20261101-091847-9.jpg", None), (14, "20261101-091848-9.jpg", "găină"), (14, "20261101-091848-9.jpg", None)]


def test_verdictul_ajunge_in_index(monkeypatch):
    import ai_client
    import semantic
    apeluri = []
    monkeypatch.setattr(ai_client, "remote", lambda: False)
    monkeypatch.setattr(semantic, "actualizeaza_scena", lambda cam, fn, et: apeluri.append((cam, fn, et)) or True)
    scena_web.feedback_adauga(14, "i1", "gresit", eticheta="pisică", poza="20261101-091845-7.jpg")
    scena_web.feedback_adauga(14, "i2", "ok", poza="20261101-091846-8.jpg")
    scena_web.feedback_adauga(14, "i3", "gresit", eticheta="nimic", poza="20261101-091847-9.jpg")
    assert apeluri == [(14, "20261101-091845-7.jpg", "pisică"), (14, "20261101-091847-9.jpg", "nimic")]
    monkeypatch.setattr(ai_client, "remote", lambda: True)
    monkeypatch.setattr(ai_client, "semantic_scena", lambda cam, fn, et: apeluri.append(("ai", cam, fn, et)))
    scena_web.feedback_adauga(14, "i4", "gresit", eticheta="cal", poza="20261101-091848-1.jpg")
    assert apeluri[-1] == ("ai", 14, "20261101-091848-1.jpg", "cal")


def test_callback_data_scurt_si_parsabil():
    d = scena_web._date("et12", 101, "710ef3f64d0c", "20261027-224256-248.jpg")
    assert len(d.encode()) <= 64
    assert scena_web._parseaza(d) == ("et12", 101, "710ef3f64d0c", "20261027-224256-248.jpg")
    assert scena_web._parseaza("altceva") is None
    assert scena_web._parseaza("sc|ok|x|id|p") is None
    taste = scena_web._taste(14, "id1", "p.jpg")["inline_keyboard"][0]
    assert [b["callback_data"].split("|")[1] for b in taste] == ["ok", "nu", "re"]


def _cq(act, cam=14, eid="id1", poza="20261027-224256-248", cine="Stefan"):
    return {"id": "cq1", "data": f"sc|{act}|{cam}|{eid}|{poza}", "from": {"first_name": cine},
            "message": {"message_id": 5, "chat": {"id": 42}, "caption": "🧩 cam14: necunoscut\nE corect?"}}


def test_butoanele_scriu_feedback(_izolare):
    apeluri = _izolare
    assert "notat" in scena_web.callback(_cq("ok"), CFG).lower()
    fb = scena_web.feedback_citeste(14)["id1"]
    assert fb["verdict"] == "ok" and fb["cine"] == "telegram:Stefan" and fb["poza"].endswith(".jpg")
    assert apeluri[-1][0] == "editMessageCaption" and "✓" in apeluri[-1][1]["caption"]
    # ✕ deschide vocabularul ca butoane; et1 = „pisica”
    scena_web.callback(_cq("nu", eid="id2"), CFG)
    assert apeluri[-1][0] == "editMessageReplyMarkup"
    randuri = apeluri[-1][1]["reply_markup"]["inline_keyboard"]
    assert randuri[0][1]["callback_data"].startswith("sc|et1|14|id2|")
    scena_web.callback(_cq("et1", eid="id2"), CFG)
    assert scena_web.feedback_citeste(14)["id2"]["eticheta"] == "pisică"
    scena_web.callback(_cq("ns", eid="id3"), CFG)
    assert scena_web.feedback_citeste(14)["id3"]["verdict"] == "necunoscut"
    assert any(b["callback_data"].startswith("sc|nm|") for r in randuri for b in r)   # „nimic nou”
    scena_web.callback(_cq("nm", eid="id6"), CFG)
    r6 = scena_web.feedback_citeste(14)["id6"]
    assert (r6["verdict"], r6["eticheta"]) == ("gresit", "nimic")
    assert not scena_web.de_trimis({"cam": 1, "id": "z", "poza": "p.jpg", "eticheta": "nimic"}, now=1)
    assert "schimbat" in scena_web.callback(_cq("et9", eid="id4"), CFG)
    assert scena_web.callback({"data": "nu e al nostru"}, CFG) is None


def test_reanalizarea_din_buton(_izolare, monkeypatch):
    import ai_client
    apeluri = _izolare
    monkeypatch.setattr(ai_client, "scena_reanalizeaza", lambda cam, eid, poza: {
        "ok": True, "eticheta": "tractor", "sursa": "vlm", "vlm": "tractor roșu",
        "yolo": {"eticheta": "truck", "incredere": 0.4}, "vocab": [{"nume": "tractor", "scor": 0.12}]})
    # sincron, ca din job
    r = scena_web.reanalizeaza(14, "id1", "p.jpg")
    assert r["eticheta"] == "tractor" and scena_web._analize[(14, "id1")] == "tractor"
    txt = scena_web.text_analiza(r)
    assert "tractor" in txt and "YOLO" in txt and "roșu" in txt
    # din Telegram: raspuns imediat, apoi mesaj cu butoanele analizei (in fir -> il chemam direct)
    assert "Analizez" in scena_web.callback(_cq("re", eid="id5"), CFG)
    scena_web._reanalizeaza_telegram(CFG, _cq("re")["message"], 14, "id5", "p.jpg")
    assert apeluri[-1][0] == "sendMessage" and apeluri[-1][1]["reply_to_message_id"] == 5
    assert apeluri[-1][1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"].startswith("sc|okA|")
    # ✓ pe analiza: eticheta propusa de VLM intra in feedback (verdict gresit = s-a schimbat)
    msg = {"message_id": 9, "chat": {"id": 42}, "text": "🔍 Analiză"}
    scena_web.callback({"data": "sc|okA|14|id5|p", "from": {"first_name": "S"}, "message": msg}, CFG)
    fb = scena_web.feedback_citeste(14)["id5"]
    assert fb["verdict"] == "gresit" and fb["eticheta"] == "tractor" and fb["sursa"] == "analiza"
    assert apeluri[-1][0] == "editMessageText"


def test_curatarea_decupajelor_pastreaza_verdictele(tmp_path):
    import time as _t
    d = tmp_path / "14"
    d.mkdir()
    for fn in ("vechi.jpg", "cu-verdict.jpg", "nou.jpg", "fundal.jpg"):
        (d / fn).write_bytes(b"x")
    vechi = _t.time() - 100 * 86400
    for fn in ("vechi.jpg", "cu-verdict.jpg", "fundal.jpg"):
        os.utime(d / fn, (vechi, vechi))
    scena_web.feedback_adauga(14, "id1", "ok", poza="cu-verdict.jpg")
    assert scena_web.curata(90) == 1
    assert sorted(os.listdir(d)) == ["cu-verdict.jpg", "fundal.jpg", "nou.jpg"]
    assert scena_web.curata(90) == 0


def test_text_analiza_arata_doar_numele_clare():
    v = [{"nume": "mașină", "scor": 0.1114}, {"nume": "ploaie", "scor": 0.076}, {"nume": "om cu cărucior", "scor": 0.0743}]
    txt = scena_web.text_analiza({"eticheta": "mașină", "vocab": v})
    assert "mașină 111" in txt and "ploaie" not in txt and "cărucior" not in txt
    txt = scena_web.text_analiza({"eticheta": "necunoscut", "vocab": [{"nume": "ploaie", "scor": 0.05}, {"nume": "umbră", "scor": 0.049}]})
    assert "nimic clar" in txt and "ploaie 50" in txt
    txt = scena_web.text_analiza({"eticheta": "cal", "vocab": [{"nume": "cal", "scor": 0.09}, {"nume": "vacă", "scor": 0.085}]})
    assert "cal 90" in txt and "vacă 85" in txt                      # doua nume apropiate: ambele


def test_analiza_esuata_anunta(_izolare, monkeypatch):
    import ai_client
    apeluri = _izolare
    monkeypatch.setattr(ai_client, "scena_reanalizeaza", lambda cam, eid, poza: {"error": "VLM indisponibil"})
    with pytest.raises(RuntimeError):
        scena_web.reanalizeaza(14, "id1", "p.jpg")
    scena_web._reanalizeaza_telegram(CFG, {"message_id": 1, "chat": {"id": 42}}, 14, "id1", "p.jpg")
    assert "nu a reușit" in apeluri[-1][1]["text"]


def test_bucla_telegram_porneste_si_doar_cu_butoanele(monkeypatch):
    assert telegram_bot.activ()
    CFG["scena_telegram"] = False
    CFG["telegram_comenzi"] = False
    assert not telegram_bot.activ()
    CFG["telegram_comenzi"] = True
    assert telegram_bot.activ()
    CFG["scena_telegram"] = True


def test_butonul_din_chat_strain_e_refuzat(monkeypatch):
    refuzate, raspunsuri = [], []
    import audit
    monkeypatch.setattr(audit, "log", lambda *a, **k: refuzate.append(a))
    monkeypatch.setattr(scena_web, "callback", lambda cq, cfg: raspunsuri.append(cq) or "ok")
    monkeypatch.setattr(telegram_bot.requests, "post", lambda *a, **k: None)
    telegram_bot._buton({"id": "1", "data": "sc|ok|14|id|p", "message": {"chat": {"id": 99}}}, CFG)
    assert refuzate and not raspunsuri
    telegram_bot._buton({"id": "1", "data": "sc|ok|14|id|p", "message": {"chat": {"id": 42}}}, CFG)
    assert len(raspunsuri) == 1


def test_notifica_trimite_poza_cu_butoane(tmp_path, _izolare):
    apeluri = _izolare
    os.makedirs(tmp_path / "14")
    (tmp_path / "14" / "p.jpg").write_bytes(b"\xff\xd8jpeg")
    e = {"cam": 14, "id": "id1", "poza": "p.jpg", "eticheta": "necunoscut", "incredere": 0,
         "sursa": "-", "t": "2026-10-27 22:42:56"}
    assert scena_web.notifica(e)
    import time
    for _ in range(50):
        if apeluri:
            break
        time.sleep(0.05)
    metoda, d, fisiere = apeluri[-1]
    assert metoda == "sendPhoto" and fisiere["photo"][1] == b"\xff\xd8jpeg"
    assert "cam14" in d["caption"] and "22:42" in d["caption"]
    assert json.loads(json.dumps(d["reply_markup"]))["inline_keyboard"][0][2]["callback_data"].startswith("sc|re|14|id1|p")
