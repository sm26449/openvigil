"""Legatura directa in redare din notificari (notify.link): doar cu web_url setat."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import notify  # noqa: E402


def test_fara_web_url_nimic(monkeypatch):
    monkeypatch.setattr(notify.settings, "get", lambda k: "")
    assert notify.link(14) == ""


def test_link_cu_zi_locala_si_10s_inainte(monkeypatch):
    monkeypatch.setattr(notify.settings, "get", lambda k: "http://nvr.local:8079/")
    ts = time.mktime((2026, 10, 23, 12, 30, 15, 0, 0, -1))
    assert notify.link(14, ts) == "\n🔗 http://nvr.local:8079/#pb/14/2026-10-23/45005"
    ts0 = time.mktime((2026, 10, 23, 0, 0, 5, 0, 0, -1))
    assert notify.link(3, ts0).endswith("/#pb/3/2026-10-23/0")      # nu sub zero
    assert notify.link(None, ts) == ""                               # eveniment de sistem
