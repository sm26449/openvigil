"""Verdictele de pe Telegram pe numerele de inmatriculare (app/numar_feedback.py): callback_data
sub 64 de octeti, citirea scoasa din log.jsonl la „✕ greșit", randul de feedback, butoanele in
notificare, dispecerul din telegram_bot."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import notify  # noqa: E402
import numar_feedback as N  # noqa: E402
import plates  # noqa: E402
import scena_web  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(plates, "PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setattr(plates, "CROPS_DIR", str(tmp_path / "plates" / "crops"))
    monkeypatch.setattr(plates, "LOG_FILE", str(tmp_path / "plates" / "log.jsonl"))
    monkeypatch.setattr(plates, "FB_FILE", str(tmp_path / "plates" / "feedback.jsonl"))


def _log(rows):
    os.makedirs(plates.PLATES_DIR, exist_ok=True)
    with open(plates.LOG_FILE, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _crop_file(rel):
    p = os.path.join(plates.PLATES_DIR, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"jpeg")


def _fb():
    with open(plates.FB_FILE, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_date_si_parseaza():
    d = N.date("ok", 14, "2026-11-06", "143022", "B123XYZ")
    assert d == "nv|ok|14|20261106|143022|B123XYZ" and len(d.encode()) <= 64
    assert N._parseaza(d) == ("ok", 14, "2026-11-06", "143022", "B123XYZ")
    assert N._parseaza("nv|ok|14|20261106|143022|b123") is None       # placa cu minuscule
    assert N._parseaza("dt|ok|14|20261106|143022|B123XYZ") is None
    assert N.e_al_nostru(d) and not N.e_al_nostru("fv|ok|6|x")
    assert N.din_crop(14, "crops/2026-11-06/143022_B123XYZ.jpg", "B123XYZ") == ("2026-11-06", "143022")
    assert N.din_crop(14, "crops/2026-11-06/143022_B123XYZ.jpg", "ALTA") is None
    assert N.din_crop(14, None, "B123XYZ") is None
    k = N.taste(14, "2026-11-06", "143022", "B123XYZ")["inline_keyboard"][0]
    assert [b["callback_data"].split("|")[1] for b in k] == ["ok", "nu"]


def test_feedback_scoate_din_log():
    crop = "crops/2026-11-06/143022_B123XYZ.jpg"
    _crop_file(crop)
    _log([{"t": "2026-11-06 14:00:00", "cam": 14, "numar": "ALTA999", "crop": "crops/2026-11-06/140000_ALTA999.jpg"},
          {"t": "2026-11-06 14:30:22", "cam": 14, "numar": "B123XYZ", "crop": crop}])
    r = plates.feedback(14, "B123XYZ", "gresit", crop=crop, cine="telegram:SM")
    assert r["scos"] is True
    ramase = [json.loads(l) for l in open(plates.LOG_FILE) if l.strip()]
    assert [x["numar"] for x in ramase] == ["ALTA999"]               # a ramas doar cealalta
    assert not os.path.exists(os.path.join(plates.PLATES_DIR, crop))  # si crop-ul sters
    assert _fb()[-1]["verdict"] == "gresit" and _fb()[-1]["numar"] == "B123XYZ"


def test_feedback_ok_pastreaza_logul():
    crop = "crops/2026-11-06/143022_B123XYZ.jpg"
    _log([{"t": "2026-11-06 14:30:22", "cam": 14, "numar": "B123XYZ", "crop": crop}])
    r = plates.feedback(14, "B123XYZ", "ok", crop=crop)
    assert r["scos"] is False
    assert len([l for l in open(plates.LOG_FILE) if l.strip()]) == 1
    assert _fb()[-1]["verdict"] == "ok"
    with pytest.raises(ValueError):
        plates.feedback(14, "B123XYZ", "habar", crop=crop)


def test_drop_reading_fara_potrivire():
    _log([{"t": "x", "cam": 14, "numar": "B123XYZ", "crop": "crops/2026-11-06/143022_B123XYZ.jpg"}])
    assert plates.drop_reading(14, "B123XYZ", "crops/2026-11-06/999999_B123XYZ.jpg") is False
    assert plates.drop_reading(99, "B123XYZ", "crops/2026-11-06/143022_B123XYZ.jpg") is False
    assert len([l for l in open(plates.LOG_FILE) if l.strip()]) == 1


def test_callback(monkeypatch):
    crop = "crops/2026-11-06/143022_B123XYZ.jpg"
    _crop_file(crop)
    _log([{"t": "2026-11-06 14:30:22", "cam": 14, "numar": "B123XYZ", "crop": crop}])
    apeluri = []
    monkeypatch.setattr(scena_web, "_tg", lambda cfg, metoda, fisiere=None, **d: apeluri.append((metoda, d)))
    msg = {"chat": {"id": 1}, "message_id": 7, "caption": "🚗 Poartă: număr B123XYZ"}
    cq = {"from": {"first_name": "SM"}, "message": msg, "data": N.date("nu", 14, "2026-11-06", "143022", "B123XYZ")}
    assert N.callback(cq, {}) == "Notat: scos din jurnal."
    assert "✕ greșit, scos din jurnal (SM)" in apeluri[-1][1]["caption"]
    assert [l for l in open(plates.LOG_FILE) if l.strip()] == []     # citirea a iesit din jurnal
    cq["data"] = N.date("ok", 14, "2026-11-06", "143022", "B123XYZ")
    assert N.callback(cq, {}) == "Mulțumesc, notat."
    assert "✓ corect (SM)" in apeluri[-1][1]["caption"]
    assert N.callback({"data": "fv|ok|6|x"}, {}) is None


def test_notificarea_are_butoane(monkeypatch):
    trimise = []
    monkeypatch.setattr(notify.settings, "get", lambda k: {"lpr_notify_all": True, "web_url": ""}.get(k))
    monkeypatch.setattr(notify, "_cam_name", lambda c: "Poartă")
    monkeypatch.setattr(notify, "send", lambda text, jpeg=None, key=None, sync=False, taste=None: trimise.append((text, taste)))
    notify.plate(14, "B123XYZ", None, b"jpg", crop="crops/2026-11-06/143022_B123XYZ.jpg")
    text, taste = trimise[-1]
    assert "Numărul e corect?" in text and len(taste["inline_keyboard"][0]) == 2
    assert taste["inline_keyboard"][0][0]["callback_data"] == "nv|ok|14|20261106|143022|B123XYZ"
    notify.plate(14, "B123XYZ", None, b"jpg", crop=None)
    assert trimise[-1][1] is None and "Numărul e corect?" not in trimise[-1][0]


def test_telegram_dispecer(monkeypatch):
    import telegram_bot
    vazut = []
    monkeypatch.setattr(N, "callback", lambda cq, cfg: vazut.append("nv") or "N")
    monkeypatch.setattr(telegram_bot.requests, "post", lambda *a, **k: None)
    cfg = {"telegram_chat": "1", "telegram_token": "t"}
    telegram_bot._buton({"id": "q", "message": {"chat": {"id": 1}}, "data": "nv|ok|14|20261106|143022|B123XYZ"}, cfg)
    assert vazut == ["nv"]
