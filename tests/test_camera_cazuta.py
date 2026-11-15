"""Camera cazuta: alerte exponentiale pana revine; continuitatea anunta o problema o singura data."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import continuity  # noqa: E402
import escaladare  # noqa: E402
import notify  # noqa: E402

MIN = 60


def _program(pana_la):
    """Momentele (minute de la cadere) la care pleaca alertele, simuland o verificare pe minut."""
    n, out = 1, [0]
    for m in range(1, pana_la + 1):
        if notify.memento_scadent(0, n, m * MIN):
            n += 1
            out.append(m)
    return out


def test_program_exponential_apoi_zilnic():
    zile = _program(4 * 1440)
    assert zile[:11] == [0, 4, 8, 16, 32, 60, 120, 240, 480, 960, 1440]
    assert zile[11:] == [2880, 4320, 5760]          # apoi o data pe zi, fara sfarsit


@pytest.mark.parametrize("sec,text", [(30, "1 min"), (7 * MIN, "7 min"), (3 * 3600, "3 h"),
                                      (3 * 3600 + 600, "3 h 10 min"), (26 * 3600, "1 zi 2 h"),
                                      (13 * 86400 + 4 * 3600, "13 zile 4 h")])
def test_durata(sec, text):
    assert notify.durata(sec) == text


@pytest.fixture
def ceas(monkeypatch, tmp_path):
    """Watchdog-ul izolat: stare pe disc in tmp, alertele capturate."""
    monkeypatch.setattr(notify, "CAZUTE_FILE", str(tmp_path / "cazute.json"))
    monkeypatch.setattr(notify, "_states", {})
    monkeypatch.setattr(notify, "_fails", {})
    monkeypatch.setattr(notify, "_schimbare_cam", lambda cid, nou: schimbari.append((cid, nou)))
    monkeypatch.setattr(notify, "_de_cand_cazuta", lambda cid: None)
    trimise, schimbari = [], []
    monkeypatch.setattr(escaladare, "alerta", lambda tip, text, **kw: trimise.append((text, kw)))
    return trimise, schimbari


CAM = {"id": 3, "name": "poarta", "ip": "10.0.0.13"}


def test_cazuta_mementouri_si_revenire(ceas):
    trimise, schimbari = ceas
    notify._verifica_cam(CAM, True, 0)                 # pornire: ok -> inchide episod vechi (tacut)
    trimise.clear()
    for m in range(1, 70):                             # cade la minutul 1; 2 esecuri -> offline
        notify._verifica_cam(CAM, False, m * MIN)
    texte = [x[0] for x in trimise]
    assert texte[0].startswith("⚠️ Camera OFFLINE") and trimise[0][1]["episod"] == "cam-3"
    assert len(texte) == 6 and all("tot OFFLINE" in x for x in texte[1:])   # 4, 8, 16, 32, 60 min
    assert "idempotency" not in str(trimise[1][1]) and "episod" not in trimise[1][1]
    notify._verifica_cam(CAM, True, 70 * MIN)
    text, kw = trimise[-1]
    assert text.startswith("✅ Camera revenita: poarta") and "după 1 h 8 min" in text
    assert kw["rezolvat"] and kw["episod"] == "cam-3"
    assert schimbari == [(3, False), (3, True)]
    assert notify._cazute() == {}


def test_repornire_continua_programul(ceas, monkeypatch):
    trimise, schimbari = ceas
    notify._scrie_cazute({"3": {"de_la": 0, "n": 3}})      # cazuta de dinainte, 3 alerte trimise
    notify._verifica_cam(CAM, False, 10 * MIN)                # primul esec dupa pornire: asteptam
    notify._verifica_cam(CAM, False, 11 * MIN)
    assert trimise == [] and schimbari == []                   # treapta urmatoare e la 16 min
    notify._verifica_cam(CAM, False, 16 * MIN)
    assert "tot OFFLINE, de 16 min" in trimise[0][0]


def test_cazuta_de_mult_la_pornire_trece_pe_zilnic(ceas, monkeypatch):
    trimise, _ = ceas
    monkeypatch.setattr(notify, "_de_cand_cazuta", lambda cid: 0)
    acum = 13 * 86400
    notify._verifica_cam(CAM, False, acum)
    notify._verifica_cam(CAM, False, acum + MIN)
    assert "OFFLINE de 13 zile" in trimise[0][0]
    notify._verifica_cam(CAM, False, acum + 2 * 3600)
    assert len(trimise) == 1                                   # urmatorul memento: peste o zi


def test_revenita_cat_serverul_era_oprit(ceas):
    trimise, schimbari = ceas
    notify._scrie_cazute({"3": {"de_la": 0, "n": 5}})
    notify._verifica_cam(CAM, True, 3 * 3600)
    assert trimise[0][0].endswith("după 3 h") and schimbari == [(3, True)]


def test_continuitate_anunta_o_data_si_inchide():
    mut = {"cheie": "mut-3", "cam_id": 3, "text": "poarta: nu s-a mai scris nimic"}
    gol = {"cheie": "gol-3-5", "cam_id": 3, "text": "poarta: gol de 71 min"}
    alt = {"cheie": "gol-7-3", "cam_id": 7, "text": "curte: gol de 12 min"}
    probs = continuity.de_anuntat([mut, gol, alt])
    assert probs == [mut, alt]                                 # golul camerei mute e aceeasi poveste
    noi, rez, stare = continuity.schimbari({}, probs, acum=1)
    assert noi == [mut, alt] and rez == []
    noi, rez, stare = continuity.schimbari(stare, probs, acum=2)
    assert noi == [] and rez == [] and stare["mut-3"]["de_la"] == 1
    noi, rez, stare = continuity.schimbari(stare, [alt], acum=3)
    assert noi == [] and [r["cheie"] for r in rez] == ["mut-3"] and list(stare) == ["gol-7-3"]
