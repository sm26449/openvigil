"""Nivelul de arhiva (record.py): segmentele cu evenimente se MUTA pe al doilea disc cand
expira retentia primara, cele goale se sterg, arhiva expira dupa arhiva_zile; _files vede
ambele niveluri; fara arhiva_dir totul merge ca inainte."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import lant  # noqa: E402
import record  # noqa: E402
import settings  # noqa: E402


@pytest.fixture
def mediu(tmp_path, monkeypatch):
    rec, arh = tmp_path / "rec", tmp_path / "arh"
    rec.mkdir(); arh.mkdir()
    monkeypatch.setattr(record, "REC_DIR", str(rec))
    monkeypatch.setattr(record, "TMP_DIR", str(tmp_path / "tmp"))
    lant.reset()
    monkeypatch.setattr(lant, "DB_FILE", str(tmp_path / "segmente.db"))
    cfg = {"retention_days": 2, "retention_event_days": 10, "retention_min_free_gb": 0,
           "arhiva_dir": str(arh), "arhiva_zile": 30, "arhiva_min_free_gb": 0}
    monkeypatch.setattr(settings, "get", lambda k: cfg.get(k))
    monkeypatch.setattr(record, "_cam_retention", lambda cid: (cfg["retention_days"], cfg["retention_event_days"]))
    evs = {}
    monkeypatch.setattr(record, "event_times", lambda cid, days=3: evs.get(cid, []))
    yield rec, arh, cfg, evs
    lant.reset()


def _seg(d, cam, epoch, continut=b"v"):
    (d / f"cam{cam}").mkdir(exist_ok=True)
    nume = time.strftime("%Y-%m-%d_%H-%M-%SZ.mp4", time.gmtime(epoch))
    p = d / f"cam{cam}" / nume
    p.write_bytes(continut)
    return str(p), nume


def test_mutare_in_arhiva_si_expirare(mediu):
    rec, arh, cfg, evs = mediu
    now = time.time()
    vechi_ev, n_ev = _seg(rec, 5, now - 3 * 86400)        # 3 zile, cu eveniment -> arhiva
    vechi_gol, n_gol = _seg(rec, 5, now - 3 * 86400 + 600)  # 3 zile, fara -> sters
    nou, n_nou = _seg(rec, 5, now - 3600)                 # proaspat -> ramane
    evs[5] = [now - 3 * 86400 + 10]
    lant.inregistreaza(5, now - 3 * 86400, vechi_ev)      # amprenta de la inchidere
    record._prune_body()
    assert not os.path.exists(vechi_ev) and os.path.exists(str(arh / "cam5" / n_ev))
    assert not os.path.exists(vechi_gol)
    assert os.path.exists(nou)
    r = lant.get(5, n_ev)
    assert r["cale"] == str(arh / "cam5" / n_ev) and r["arhivat"]
    # _files vede ambele niveluri, in ordine; fara arhiva doar primarul
    toate = record._files(5)
    assert [fn for _, _, fn in toate] == [n_ev, n_nou]
    assert [fn for _, _, fn in record._files(5, arhiva=False)] == [n_nou]
    assert record.has_recordings(5)
    # expirarea arhivei
    cfg["arhiva_zile"] = 1
    assert record._prune_arhiva() == 1 and not os.path.exists(str(arh / "cam5" / n_ev))


def test_copie_cu_hash_gresit_ramane_pe_primar(mediu, monkeypatch):
    rec, arh, cfg, evs = mediu
    now = time.time()
    p, nume = _seg(rec, 6, now - 3 * 86400)
    lant.inregistreaza(6, now - 3 * 86400, p)
    with open(p, "ab") as f:                      # fisierul s-a schimbat dupa amprenta
        f.write(b"x")
    assert record._arhiveaza(6, now - 3 * 86400, p, nume) is False
    assert os.path.exists(p) and not os.path.exists(str(arh / "cam6" / nume))


def test_fara_arhiva_comportamentul_vechi(mediu):
    rec, arh, cfg, evs = mediu
    cfg["arhiva_dir"] = ""
    now = time.time()
    p, nume = _seg(rec, 5, now - 3 * 86400)
    evs[5] = [now - 3 * 86400 + 10]
    record._prune_body()
    assert os.path.exists(p)                      # cu eveniment, sub ev_days: ramane pe primar
    assert record.archive_dir() is None and record._prune_arhiva() == 0
