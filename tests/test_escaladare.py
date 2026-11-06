"""Clientul de escaladare (app/escaladare.py): gating pe configurare, payload-ul /v1/fire,
antetul X-API-Key, best-effort fără excepții."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import escaladare as E  # noqa: E402


class _Resp:
    def __init__(self, ok=True, status=200, text=""):
        self.ok, self.status_code, self.text = ok, status, text


def _cfg(monkeypatch, **vals):
    d = {"alert_service_url": "http://127.0.0.1:5090/v1/fire",
         "alert_service_key": "alertd_test", "alert_service_type": "nvr_cadere"}
    d.update(vals)
    monkeypatch.setattr(E.settings, "get", lambda k: d[k])


def test_oprit_fara_cheie(monkeypatch):
    _cfg(monkeypatch, alert_service_key="")
    trimise = []
    monkeypatch.setattr(E.requests, "post", lambda *a, **k: trimise.append(1) or _Resp())
    assert E.activ() is False
    assert E.escaladeaza({"cam": 6}, sync=True) is False
    assert trimise == []                       # nimic nu a plecat


def test_payload_si_antet(monkeypatch):
    _cfg(monkeypatch)
    captat = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captat.update(url=url, json=json, headers=headers, timeout=timeout)
        return _Resp()
    monkeypatch.setattr(E.requests, "post", fake_post)
    assert E.escaladeaza({"nume_cam": "Curte", "cam": 6}, sync=True) is True
    assert captat["url"] == "http://127.0.0.1:5090/v1/fire"
    assert captat["headers"]["X-API-Key"] == "alertd_test"
    assert captat["json"] == {"type": "nvr_cadere", "context": {"nume_cam": "Curte", "cam": 6}}
    assert captat["timeout"] and captat["timeout"] <= 15


def test_tip_suprascris(monkeypatch):
    _cfg(monkeypatch)
    captat = {}
    monkeypatch.setattr(E.requests, "post",
                        lambda url, json=None, headers=None, timeout=None: captat.update(json=json) or _Resp())
    E.escaladeaza({"x": 1}, tip="nvr_flacara", sync=True)
    assert captat["json"]["type"] == "nvr_flacara"


def test_nu_ridica_la_eroare(monkeypatch):
    _cfg(monkeypatch)

    def boom(*a, **k):
        raise ConnectionError("alertd jos")
    monkeypatch.setattr(E.requests, "post", boom)
    # best-effort: întoarce True (a încercat), dar nu propagă excepția
    assert E.escaladeaza({"cam": 6}, sync=True) is True


def test_http_ne_ok_nu_ridica(monkeypatch):
    _cfg(monkeypatch)
    monkeypatch.setattr(E.requests, "post",
                        lambda *a, **k: _Resp(ok=False, status=401, text="bad key"))
    assert E.escaladeaza({"cam": 6}, sync=True) is True
