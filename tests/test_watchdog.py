"""Debounce-ul watchdog-ului de camere: un timeout ISAPI singular nu e o cadere.

Ancheta 2026-09-11: "camerele 1-2-3 pica impreuna" era un fals-alarm sistematic —
NVR-ul isi plimba config-push-ul prin camere in ordinea canalelor si le ocupa
serverul web ~o matura de watchdog, cu inregistrarea curgand fara niciun gol.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import notify  # noqa: E402


def test_un_esec_singular_nu_declara_offline():
    # online cunoscut + primul esec -> ramane online (fara alarma)
    assert notify._stare_noua(True, ok=False, esecuri=1) is True


def test_doua_esecuri_consecutive_declara_offline():
    assert notify._stare_noua(True, ok=False, esecuri=2) is False
    assert notify._stare_noua(True, ok=False, esecuri=5) is False


def test_revenirea_e_instant():
    assert notify._stare_noua(False, ok=True, esecuri=0) is True


def test_offline_ramane_offline_cat_esueaza():
    assert notify._stare_noua(False, ok=False, esecuri=7) is False


def test_scenariul_real_cam1_13_03():
    """Fereastra reala din 2026-09-09 13:03: O singura matura esuata, apoi succes
    — cu debounce, niciun eveniment nu s-ar fi emis."""
    stare, fails, tranzitii = True, 0, []
    for ok in (True, False, True, True):        # maturi succesive
        fails = 0 if ok else fails + 1
        noua = notify._stare_noua(stare, ok, fails)
        if noua != stare:
            tranzitii.append(noua)
        stare = noua
    assert tranzitii == []                       # zero alarme false


def _nvr_cu_raspuns(monkeypatch, ok):
    """ISAPI deviceInfo al NVR-ului raspunde (ok) sau nu (timeout)."""
    class _R:
        def __init__(self):
            self.ok = ok
    class _S:
        auth = None
        def get(self, *a, **k):
            if not ok:
                raise notify.requests.RequestException("timeout")
            return _R()
    monkeypatch.setattr(notify.requests, "Session", _S)
    monkeypatch.setattr(notify.settings, "nvr_retras", lambda: False)   # testul nu depinde de settings.json
    trimise = []
    monkeypatch.setattr(notify, "send", lambda text, **k: trimise.append(text))
    notify._states.pop("nvr", None)
    notify._fails.pop("nvr", None)
    return trimise


def test_nvr_cazut_de_la_pornire_e_semnalat_o_singura_data(monkeypatch):
    trimise = _nvr_cu_raspuns(monkeypatch, ok=False)
    notify._check_nvr()                       # primul esec: inca nu stim nimic
    assert trimise == [] and notify.stare_nvr() is None
    notify._check_nvr()                       # al doilea la rand: offline, spus o data
    assert len(trimise) == 1 and "de la pornire" in trimise[0] and notify.stare_nvr() is False
    notify._check_nvr()
    assert len(trimise) == 1


def test_nvr_un_timeout_singular_nu_e_cadere(monkeypatch):
    trimise = _nvr_cu_raspuns(monkeypatch, ok=False)
    notify._states["nvr"] = True              # era online la verificarea de dinainte
    notify._check_nvr()
    assert notify.stare_nvr() is True and trimise == []       # un singur esec = nimic
    notify._check_nvr()
    assert notify.stare_nvr() is False and len(trimise) == 1 and "OFFLINE" in trimise[0]


def test_nvr_revine_instant(monkeypatch):
    trimise = _nvr_cu_raspuns(monkeypatch, ok=True)
    notify._states["nvr"] = False
    notify._check_nvr()
    assert notify.stare_nvr() is True and len(trimise) == 1 and "revenit" in trimise[0]
