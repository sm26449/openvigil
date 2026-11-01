"""Verdictele de pe Telegram pe detectiile YOLO (app/detectie_feedback.py): callback_data sub 64
de octeti, decupajul din sidecarul snapshotului in data/scena/<cam>/, randul de feedback cu ce
propusese YOLO, butoanele in notificare, dispecerul din telegram_bot."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import detectie_feedback as D  # noqa: E402
import notify  # noqa: E402
import scena_fundal  # noqa: E402
import scena_web  # noqa: E402
import snaps  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(snaps, "SNAP_DIR", str(tmp_path / "snapshots"))
    monkeypatch.setattr(scena_web, "SCENA_DIR", str(tmp_path / "scena"))
    monkeypatch.setattr(scena_web, "FEEDBACK", str(tmp_path / "scena" / "feedback.jsonl"))
    monkeypatch.setattr(scena_web, "_fundal_invata", lambda cam, poza, r: None)
    monkeypatch.setattr(scena_web, "_index_eticheta", lambda cam, poza, et: None)
    monkeypatch.setattr(scena_web, "vocabular", lambda: ["găină", "mașină"])


def _snapshot(tmp_path, cu_sidecar=True):
    Image = pytest.importorskip("PIL.Image")
    d = tmp_path / "snapshots" / "2026-11-01"
    d.mkdir(parents=True)
    img = Image.new("RGB", (200, 100), (10, 10, 10))
    for x in range(100, 160):
        for y in range(20, 80):
            img.putpixel((x, y), (200, 200, 200))
    img.save(d / "121631_cam6_person.jpg")
    if cu_sidecar:
        (d / "121631_cam6_person.jpg.json").write_text(json.dumps({"boxes": [
            {"cls": "dog", "x": 0.0, "y": 0.0, "w": 0.1, "h": 0.1, "conf": 0.9},
            {"cls": "person", "x": 0.5, "y": 0.2, "w": 0.3, "h": 0.6, "conf": 0.61},
            {"cls": "person", "x": 0.1, "y": 0.1, "w": 0.1, "h": 0.1, "conf": 0.3}]}))


def test_date_si_parseaza():
    d = D.date("ok", 102, "2026-11-01", "121631", "motorcycle")
    assert d == "dt|ok|102|20261101|121631|motorcycle" and len(d.encode()) <= 64
    assert D._parseaza(d) == ("ok", 102, "2026-11-01", "121631", "motorcycle")
    assert D._parseaza("sc|ok|14|abc|x") is None and D._parseaza("dt|ok|6|2026|121631|person") is None
    assert D.e_al_nostru(d) and not D.e_al_nostru("sc|ok|14|abc|x")
    assert D.din_poza(6, ("2026-11-01", "121631_cam6_person.jpg")) == ("2026-11-01", "121631", "person")
    assert D.din_poza(7, ("2026-11-01", "121631_cam6_person.jpg")) is None and D.din_poza(6, None) is None
    k = D.taste(6, "2026-11-01", "121631", "person")["inline_keyboard"][0]
    assert [b["callback_data"].split("|")[1] for b in k] == ["ok", "nu", "nm"]


def test_verdict_cu_decupaj(tmp_path):
    _snapshot(tmp_path)
    r = D.verdict(6, "2026-11-01", "121631", "person", "ok", cine="telegram:SM")
    assert r["eticheta_initiala"] == "person" and r["sursa_initiala"] == "yolo" and r["incredere"] == 0.61
    assert r["regiune"] == [0.5, 0.2, 0.3, 0.6] and r["cadru"] == "2026-11-01/121631_cam6_person.jpg"
    assert r["sursa"] == "detectie" and r["id"] == "d-20261101-121631-6-person"
    assert r["poza"] and r["poza"].startswith("20261101-121631-9") and r["poza"].endswith(".jpg")
    Image = pytest.importorskip("PIL.Image")
    im = Image.open(tmp_path / "scena" / "6" / r["poza"])
    assert 50 <= im.size[0] <= 60 and 50 <= im.size[1] <= 60          # chenarul (60x60) minus marginea
    assert im.getpixel((5, 5))[0] > 150                               # continutul chenarului, nu fundalul
    assert scena_fundal.nume_exemplu(r) == "person"                   # ✓ -> exemplu de obiect al camerei
    r2 = D.verdict(6, "2026-11-01", "121631", "person", "gresit", nume="nimic", cine="telegram:SM")
    assert r2["poza"] == r["poza"] and scena_fundal.nume_exemplu(r2) == "nimic"
    assert scena_web.feedback_citeste(6)[r["id"]]["eticheta"] == "nimic"   # ultimul cuvant conteaza


def test_verdict_fara_sidecar_sau_snapshot(tmp_path):
    _snapshot(tmp_path, cu_sidecar=False)
    r = D.verdict(6, "2026-11-01", "121631", "person", "ok")
    assert r["poza"] is None and r["cadru"] == "2026-11-01/121631_cam6_person.jpg" and "regiune" not in r
    r = D.verdict(6, "2026-11-01", "999999", "person", "ok")
    assert r["poza"] is None and r.get("cadru") is None and r["eticheta_initiala"] == "person"


def test_callback(tmp_path, monkeypatch):
    _snapshot(tmp_path)
    apeluri = []
    monkeypatch.setattr(scena_web, "_tg", lambda cfg, metoda, fisiere=None, **d: apeluri.append((metoda, d)))
    msg = {"chat": {"id": 1}, "message_id": 7, "caption": "🚨 Curte: Om detectat"}
    cq = {"from": {"first_name": "SM"}, "message": msg, "data": D.date("nu", 6, "2026-11-01", "121631", "person")}
    assert D.callback(cq, {}) == "Ce e de fapt?"
    taste = apeluri[-1][1]["reply_markup"]["inline_keyboard"]
    assert all(b["callback_data"].startswith("dt|") for rnd in taste for b in rnd)
    assert taste[0][0]["text"] == "găină" and taste[0][0]["callback_data"].split("|")[1] == "et0"
    cq["data"] = D.date("et0", 6, "2026-11-01", "121631", "person")
    assert D.callback(cq, {}) == "Notat: găină."
    assert scena_web.feedback_citeste(6)["d-20261101-121631-6-person"]["eticheta"] == "găină"
    assert apeluri[-1][0] == "editMessageCaption" and "✕ e găină (SM)" in apeluri[-1][1]["caption"]
    cq["data"] = D.date("nm", 6, "2026-11-01", "121631", "person")
    assert D.callback(cq, {}) == "Notat: fals."
    assert scena_web.feedback_citeste(6)["d-20261101-121631-6-person"]["eticheta"] == "nimic"
    assert D.callback({"data": "sc|ok|6|x|y"}, {}) is None


def test_notificarea_are_butoane(monkeypatch):
    trimise = []
    monkeypatch.setattr(notify.settings, "get", lambda k: {"notify_labels": ["person"], "web_url": ""}.get(k))
    monkeypatch.setattr(notify, "_cam_name", lambda c: "Curte")
    monkeypatch.setattr(notify, "send", lambda text, jpeg=None, key=None, sync=False, taste=None: trimise.append((text, taste)))
    notify.detection(6, "person", b"jpg", poza=("2026-11-01", "121631_cam6_person.jpg"))
    text, taste = trimise[-1]
    assert "E corect?" in text and len(taste["inline_keyboard"][0]) == 3
    assert taste["inline_keyboard"][0][0]["callback_data"] == "dt|ok|6|20261101|121631|person"
    notify.detection(6, "person", b"jpg", poza=None)
    assert trimise[-1][1] is None and "E corect?" not in trimise[-1][0]
    notify.detection(6, "cow", b"jpg", poza=("2026-11-01", "121631_cam6_cow.jpg"))
    assert len(trimise) == 2                                          # eticheta nealeasa: nimic


def test_telegram_dispecer(monkeypatch):
    import telegram_bot
    vazut = []
    monkeypatch.setattr(D, "callback", lambda cq, cfg: vazut.append("dt") or "D")
    monkeypatch.setattr(scena_web, "callback", lambda cq, cfg: vazut.append("sc") or "S")
    monkeypatch.setattr(telegram_bot.requests, "post", lambda *a, **k: None)
    cfg = {"telegram_chat": "1", "telegram_token": "t"}
    telegram_bot._buton({"id": "q", "message": {"chat": {"id": 1}}, "data": "dt|ok|6|20261101|121631|person"}, cfg)
    telegram_bot._buton({"id": "q", "message": {"chat": {"id": 1}}, "data": "sc|ok|6|abc|x"}, cfg)
    assert vazut == ["dt", "sc"]
