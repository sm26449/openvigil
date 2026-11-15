"""Alertele operationale prin serviciul extern (escaladare.alerta) si rezerva pe botul propriu."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import escaladare  # noqa: E402
import notify  # noqa: E402
import rules  # noqa: E402


@pytest.mark.parametrize("raspuns,rezerva", [
    (None, True),                                                    # n-a raspuns / HTTP / JSON
    ({"status": "sent"}, False),
    ({"status": "partial"}, False),
    ({"status": "pending"}, False),                                  # livrare in curs, nu esec
    ({"status": "failed"}, True),
    ({"status": "suppressed", "suppressed_reason": "unknown_type"}, True),
    ({"status": "suppressed", "suppressed_reason": "throttled"}, False),   # decizia serviciului
    ({"status": "suppressed", "suppressed_reason": "gated"}, False),
    ({"status": "repeat", "episode": "repeat"}, False),
    ({"status": "not_found", "episode": "not_found"}, True),         # episodul s-a deschis pe bot
])
def test_cand_pleaca_pe_rezerva(raspuns, rezerva):
    assert escaladare.are_nevoie_de_rezerva(raspuns) is rezerva


@pytest.fixture
def canal(monkeypatch):
    """Serviciul configurat; capteaza ce pleaca spre el si spre botul propriu."""
    cfg = {"alert_service_url": "http://x/v1/fire", "alert_service_key": "k"}
    monkeypatch.setattr(escaladare.settings, "get", lambda k: cfg.get(k))
    spre, bot, raspuns = [], [], {"r": {"status": "sent"}}
    monkeypatch.setattr(escaladare, "fire", lambda p, timeout=8: spre.append(p) or raspuns["r"])
    monkeypatch.setattr(notify, "send", lambda text, **kw: bot.append((text, kw)))
    return cfg, spre, bot, raspuns


def test_alerta_pleaca_la_serviciu_cu_episod(canal):
    _, spre, bot, _ = canal
    escaladare.alerta("nvr_camera_cazuta", "⚠️ Camera OFFLINE: Poarta", episod="cam-6",
                      revenire="nvr_camera_revenita", cam="Poarta", ip=None, sync=True)
    p = spre[0]
    assert p["type"] == "nvr_camera_cazuta" and p["source"] == "openvigil"
    assert p["idempotency_key"] == "openvigil:cam-6" and p["resolved_type"] == "nvr_camera_revenita"
    assert p["context"] == {"text": "⚠️ Camera OFFLINE: Poarta", "link": "", "cam": "Poarta"}
    assert "status" not in p and bot == []


def test_rezolvarea_inchide_episodul(canal):
    _, spre, _, _ = canal
    escaladare.alerta("nvr_camera_cazuta", "✅ revenita", episod="cam 6/x", rezolvat=True,
                      revenire="nvr_camera_revenita", sync=True)
    assert spre[0]["status"] == "resolved" and "resolved_type" not in spre[0]
    assert spre[0]["idempotency_key"] == "openvigil:cam-6/x"      # caractere permise de serviciu


def test_serviciul_cazut_trece_pe_bot(canal):
    _, _, bot, raspuns = canal
    raspuns["r"] = None
    escaladare.alerta("nvr_disc_plin", "💾 disc", key="disc", sync=True)
    assert bot == [("💾 disc", {"jpeg": None, "key": "disc", "sync": True})]


def test_fara_rezerva_nu_dubleaza_pe_bot(canal):
    _, spre, bot, raspuns = canal
    raspuns["r"] = None
    escaladare.alerta("nvr_intrus", "🚨", rezerva=False, sync=True)
    assert len(spre) == 1 and bot == []


def test_serviciu_neconfigurat_merge_direct_pe_bot(canal):
    cfg, spre, bot, _ = canal
    cfg["alert_service_key"] = ""
    escaladare.alerta("nvr_rezumat", "📋 ziua", sync=True)
    assert spre == [] and bot[0][0] == "📋 ziua"


@pytest.mark.parametrize("entry,mod,tip", [
    ({"tip": "intrus", "eticheta": "person", "sev": "atentie"}, "plecat", "nvr_intrus"),
    ({"tip": "intrus", "eticheta": "person", "sev": "critic"}, "noapte", "nvr_intrus"),
    ({"tip": "intrus", "eticheta": "person", "sev": "critic"}, "acasa", "nvr_intrus_acasa"),
    ({"tip": "intrus", "eticheta": "person", "sev": "atentie"}, "acasa", None),
    ({"tip": "intrus", "eticheta": "car", "sev": "atentie"}, "plecat", None),   # trafic, nu SMS
    ({"tip": "persoana_necunoscuta", "sev": "critic"}, "plecat", "nvr_intrus"),
    ({"tip": "persoana_necunoscuta", "sev": "critic"}, "acasa", "nvr_intrus_acasa"),
    ({"tip": "detectie", "sev": "info"}, "plecat", None),
])
def test_copia_de_intrus(entry, mod, tip):
    assert rules.tip_intrus(entry, mod) == tip


def test_disc_la_pornire_inchide_episodul_vechi(canal, monkeypatch):
    """Dupa o repornire, un episod „disc plin” ramas deschis s-ar inghiti urmatoarea alerta."""
    import collections
    _, spre, bot, raspuns = canal
    raspuns["r"] = {"status": "not_found", "episode": "not_found"}
    monkeypatch.setattr(notify, "_disk_vazut", False)
    monkeypatch.setattr(notify, "_disk_alerted", 0)
    monkeypatch.setattr("shutil.disk_usage", lambda p: collections.namedtuple("u", "free")(10**13))
    monkeypatch.setattr(escaladare.threading, "Thread",
                        lambda target, **kw: type("T", (), {"start": lambda self: target()})())
    notify._check_disk()
    notify._check_disk()                       # a doua verificare: nimic nou
    assert [(p["type"], p.get("status"), p["idempotency_key"]) for p in spre] == \
        [("nvr_disc_plin", "resolved", "openvigil:disc")]
    assert bot == []                           # not_found + fara rezerva -> tacere
