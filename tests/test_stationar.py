"""Registrul de stationare (episoade E1): parcarea e o stare cu doua tranzitii.

Masina care sta pe loc STATIONAR_MIN devine parcare (un eveniment), nu re-alerteaza
cat sta, si emite plecarea cu durata. Track-ID-urile ByteTrack se schimba — aceeasi
parcare se ADOPTA prin suprapunere, fara evenimente duplicate.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import stationar  # noqa: E402
import track  # noqa: E402

BOX = (0.40, 0.40, 0.10, 0.08)          # box normalizat: masina "parcata"
CX, CY = 450.0, 280.0                    # centrul in pixeli (doar bookkeeping)


@pytest.fixture
def curat(monkeypatch, tmp_path):
    """Registru gol, persistat in tmp, cu evenimentele capturate."""
    emise = []
    import hooks
    monkeypatch.setattr(hooks, "emit",
                        lambda tip, cam=None, **d: emise.append((tip, cam, d)))
    monkeypatch.setattr(stationar, "FILE", str(tmp_path / "stationar.json"))
    monkeypatch.setattr(stationar, "_reg", {})
    monkeypatch.setattr(stationar, "_bind", {})
    monkeypatch.setattr(stationar, "_loaded", True)
    monkeypatch.setattr(stationar, "_tick_last", 0.0)
    return emise


def _tracker():
    t = track.Tracker.__new__(track.Tracker)
    t.cam_id = 7
    t.tracks = {}
    return t


def _sta(tr, tid, now, box=BOX):
    tr.update([(tid, "car", CX, CY, box)], now)


def test_parcarea_incepe_dupa_stationar_min(curat):
    tr = _tracker()
    _sta(tr, 1, 1000.0)
    _sta(tr, 1, 1000.0 + stationar.STATIONAR_MIN - 1)
    assert curat == []                                    # inca nu e parcare
    _sta(tr, 1, 1000.0 + stationar.STATIONAR_MIN + 1)
    assert [e[0] for e in curat] == ["parcare_inceput"]
    assert stationar.suprapune(7, BOX) == "car"           # detectia o va suprima


def test_plecarea_emite_durata(curat):
    tr = _tracker()
    _sta(tr, 1, 1000.0)
    _sta(tr, 1, 1000.0 + stationar.STATIONAR_MIN + 1)
    mutat = (BOX[0] + 0.2, BOX[1], BOX[2], BOX[3])        # s-a urnit serios
    _sta(tr, 1, 1000.0 + 600, box=mutat)
    tipuri = [e[0] for e in curat]
    assert tipuri == ["parcare_inceput", "parcare_sfarsit"]
    assert curat[1][2]["durata_min"] == pytest.approx(10, abs=0.1)
    assert stationar.suprapune(7, BOX) is None            # nu se mai suprima


def test_track_nou_pe_aceeasi_parcare_se_adopta(curat):
    tr = _tracker()
    _sta(tr, 1, 1000.0)
    _sta(tr, 1, 1000.0 + stationar.STATIONAR_MIN + 1)
    # ByteTrack pierde ID-ul (ocluzie); track NOU pe acelasi loc devine si el stationar
    _sta(tr, 2, 2000.0)
    _sta(tr, 2, 2000.0 + stationar.STATIONAR_MIN + 1)
    assert [e[0] for e in curat] == ["parcare_inceput"]   # UNA singura, adoptata


def test_pierduta_din_vedere_se_inchide_cu_nota(curat):
    tr = _tracker()
    _sta(tr, 1, 1000.0)
    _sta(tr, 1, 1000.0 + stationar.STATIONAR_MIN + 1)
    stationar.tick(1000.0 + stationar.STATIONAR_MIN + stationar.NEVAZUT_MAX + 20)
    assert curat[-1][0] == "parcare_sfarsit"
    assert curat[-1][2]["nota"] == "pierdut din vedere"
    # durata pana la ULTIMA vedere (t0=1000 -> ultima _sta la +121s ~= 2 min),
    # nu pana la tick — gratia de nevazut nu e timp parcat
    assert curat[-1][2]["durata_min"] == pytest.approx(2.0, abs=0.1)


def test_vederea_din_detectie_tine_parcarea_vie(curat, monkeypatch):
    """REGRESIE 2026-09-10 11:28: track-ul a murit pe masina nemiscata si registrul
    a expirat "pierdut din vedere" desi detectia o suprima (deci o VEDEA) continuu.
    suprapune(refresh=True) e dovada de prezenta — tine `last` proaspat."""
    tr = _tracker()
    _sta(tr, 1, 1000.0)
    _sta(tr, 1, 1000.0 + stationar.STATIONAR_MIN + 1)     # parcata; track-ul moare apoi
    tarziu = 1000.0 + stationar.STATIONAR_MIN + stationar.NEVAZUT_MAX + 60
    monkeypatch.setattr(stationar.time, "time", lambda: tarziu)  # refresh scrie time.time()
    assert stationar.suprapune(7, BOX, refresh=True) == "car"    # detectia o vede
    stationar.tick(tarziu + 5)
    assert [e[0] for e in curat] == ["parcare_inceput"]   # NU s-a inchis "pierdut"
    assert stationar.suprapune(7, BOX) == "car"


def test_omul_nu_devine_parcare(curat):
    tr = _tracker()
    tr.update([(1, "person", CX, CY, BOX)], 1000.0)
    tr.update([(1, "person", CX, CY, BOX)], 1000.0 + stationar.STATIONAR_MIN + 5)
    assert curat == []


def test_camtrack_accepta_tuple_cu_box(curat, monkeypatch):
    """REGRESIE 2026-09-09: CamTrack.update (linii/zone) facea unpack pe 4 campuri;
    tuplele de 5 (cu box, pentru stationare) i-au omorat firul ai-batch o zi intreaga."""
    import ai_track
    monkeypatch.setattr(ai_track.ai_analytics, "lines",
                        lambda cid: [{"name": "l", "p1": [0.5, 0.0], "p2": [0.5, 1.0]}])
    monkeypatch.setattr(ai_track.ai_analytics, "zones", lambda cid: [])
    ct = ai_track.CamTrack(14, lambda tip, cam, date: curat.append((tip, cam, date)))
    ct._wh = (640, 360)
    ct.update([(1, "car", 200.0, 180.0, BOX)], 1000.0)          # apare in stanga
    ct.update([(1, "car", 500.0, 180.0, BOX)], 1001.5)          # trece linia
    treceri = [e for e in curat if e[0] == "trecere"]
    assert len(treceri) == 1                                    # analitica merge cu 5-tuple


def test_expire_camtrack_nu_numara_parcarea(curat):
    """Si expirarea BOGATA (CamTrack, cu viteza/zabovire) sare track-urile parcate —
    dublase scheletul si pierduse skip-ul (2026-09-09)."""
    import ai_track
    ct = ai_track.CamTrack(7, lambda *a: None)
    for i in range(7):
        ct.update([(1, "car", CX, CY, BOX)], 1000.0 + i)
    ct.update([(1, "car", CX, CY, BOX)], 1000.0 + stationar.STATIONAR_MIN + 1)
    assert ct.expire(1000.0 + stationar.STATIONAR_MIN + 100) == []


def test_expire_nu_numara_parcarea_ca_obiect(curat):
    tr = _tracker()
    for i in range(7):                                    # peste MIN_FRAMES
        _sta(tr, 1, 1000.0 + i)
    _sta(tr, 1, 1000.0 + stationar.STATIONAR_MIN + 1)
    gata = tr.expire(1000.0 + stationar.STATIONAR_MIN + 100)
    assert gata == []                                     # episodul il acopera
