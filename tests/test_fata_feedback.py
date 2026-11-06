"""Verdictele de pe Telegram pe fetele recunoscute (app/fata_feedback.py): callback_data sub 64
de octeti, crop-ul recunoscut mutat in unknown/ la „✕ nu e el", randul de feedback, butoanele in
notificare, dispecerul din telegram_bot."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import faces  # noqa: E402
import fata_feedback as F  # noqa: E402
import notify  # noqa: E402
import scena_web  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(faces, "FACES_DIR", str(tmp_path / "faces"))
    monkeypatch.setattr(faces, "RECENT_DIR", str(tmp_path / "faces" / "recent"))
    monkeypatch.setattr(faces, "UNKNOWN_DIR", str(tmp_path / "faces" / "unknown"))
    monkeypatch.setattr(faces, "FB_FILE", str(tmp_path / "faces" / "feedback.jsonl"))


def _recent(cam, zi, hms, nume, continut=b"jpeg"):
    d = os.path.join(faces.RECENT_DIR, zi)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{hms}_cam{cam}_{nume}.jpg"), "wb") as f:
        f.write(continut)


def _fb():
    with open(faces.FB_FILE, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def test_date_si_parseaza():
    d = F.date("ok", 6, "2026-11-06", "143022", "stefan")
    assert d == "fv|ok|6|20261106|143022|stefan" and len(d.encode()) <= 64
    assert F._parseaza(d) == ("ok", 6, "2026-11-06", "143022", "stefan")
    assert F._parseaza("fv|ok|6|2026|143022|stefan") is None          # zi scurta
    assert F._parseaza("fv|ok|6|20261106|143022|Stefan") is None      # nume non-ASCII/majuscula
    assert F._parseaza("dt|ok|6|20261106|143022|stefan") is None
    assert F.e_al_nostru(d) and not F.e_al_nostru("nv|ok|6|x")
    k = F.taste(6, "2026-11-06", "143022", "stefan")["inline_keyboard"][0]
    assert [b["callback_data"].split("|")[1] for b in k] == ["ok", "nu"]
    assert "stefan" in k[0]["text"] and "stefan" in k[1]["text"]


def test_feedback_muta_in_unknown():
    _recent(6, "2026-11-06", "143022", "stefan")
    r = faces.feedback(6, "stefan", "gresit", "2026-11-06", "143022", cine="telegram:SM")
    assert r["mutat"] == "unknown/2026-11-06/143022_cam6.jpg"
    assert not os.path.exists(os.path.join(faces.RECENT_DIR, "2026-11-06", "143022_cam6_stefan.jpg"))
    assert os.path.isfile(os.path.join(faces.UNKNOWN_DIR, "2026-11-06", "143022_cam6.jpg"))
    assert faces.unknown_list()[0]["cam"] == 6                        # reintra in materia de re-etichetare
    rand = _fb()[-1]
    assert rand["verdict"] == "gresit" and rand["nume"] == "stefan" and rand["cine"] == "telegram:SM"
    assert rand["crop"] == "unknown/2026-11-06/143022_cam6.jpg"


def test_feedback_ok_doar_logheaza():
    _recent(6, "2026-11-06", "143022", "stefan")
    r = faces.feedback(6, "stefan", "ok", "2026-11-06", "143022")
    assert r["mutat"] is None
    assert os.path.isfile(os.path.join(faces.RECENT_DIR, "2026-11-06", "143022_cam6_stefan.jpg"))
    assert not os.path.isdir(faces.UNKNOWN_DIR)
    assert _fb()[-1]["verdict"] == "ok"
    with pytest.raises(ValueError):
        faces.feedback(6, "stefan", "habar", "2026-11-06", "143022")


def test_feedback_fara_crop():
    """Crop-ul expirat (sters de prune) sau coliziune: nu avem ce muta, dar logam verdictul."""
    r = faces.feedback(6, "stefan", "gresit", "2026-11-06", "143022")
    assert r["mutat"] is None and _fb()[-1]["verdict"] == "gresit"


def test_callback(monkeypatch):
    _recent(6, "2026-11-06", "143022", "stefan")
    apeluri = []
    monkeypatch.setattr(scena_web, "_tg", lambda cfg, metoda, fisiere=None, **d: apeluri.append((metoda, d)))
    msg = {"chat": {"id": 1}, "message_id": 7, "caption": "👤 Curte: stefan"}
    cq = {"from": {"first_name": "SM"}, "message": msg, "data": F.date("nu", 6, "2026-11-06", "143022", "stefan")}
    assert F.callback(cq, {}) == "Notat: pus la reetichetare."
    assert apeluri[-1][0] == "editMessageCaption" and "✕ nu e stefan (SM)" in apeluri[-1][1]["caption"]
    assert _fb()[-1]["verdict"] == "gresit"
    cq["data"] = F.date("ok", 6, "2026-11-06", "143022", "maria")
    _recent(6, "2026-11-06", "143022", "maria")
    assert F.callback(cq, {}) == "Mulțumesc, notat."
    assert "✓ e maria (SM)" in apeluri[-1][1]["caption"]
    assert F.callback({"data": "nv|ok|6|x"}, {}) is None


def test_notificarea_are_butoane(monkeypatch):
    trimise = []
    monkeypatch.setattr(notify.settings, "get", lambda k: {"face_notify": True, "web_url": ""}.get(k))
    monkeypatch.setattr(notify, "_cam_name", lambda c: "Curte")
    monkeypatch.setattr(notify, "send", lambda text, jpeg=None, key=None, sync=False, taste=None: trimise.append((text, taste)))
    notify.face(6, "stefan", b"jpg", ref=("2026-11-06", "143022"))
    text, taste = trimise[-1]
    assert "E corect?" in text and len(taste["inline_keyboard"][0]) == 2
    assert taste["inline_keyboard"][0][0]["callback_data"] == "fv|ok|6|20261106|143022|stefan"
    notify.face(6, "stefan", b"jpg", ref=None)
    assert trimise[-1][1] is None and "E corect?" not in trimise[-1][0]


def test_telegram_dispecer(monkeypatch):
    import telegram_bot
    vazut = []
    monkeypatch.setattr(F, "callback", lambda cq, cfg: vazut.append("fv") or "F")
    monkeypatch.setattr(telegram_bot.requests, "post", lambda *a, **k: None)
    cfg = {"telegram_chat": "1", "telegram_token": "t"}
    telegram_bot._buton({"id": "q", "message": {"chat": {"id": 1}}, "data": "fv|ok|6|20261106|143022|stefan"}, cfg)
    assert vazut == ["fv"]
