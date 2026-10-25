"""LPR: citiri confirmate intre praguri, statistici, alerta la masini necunoscute;
Face ID: gruparea fetelor necunoscute (fara model: embeddings sintetice)."""
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import faces    # noqa: E402
import notify   # noqa: E402
import plates   # noqa: E402


def test_confirmata_cere_doua_citiri_in_fereastra():
    plates._pending.clear()
    t = 1000.0
    assert plates.confirmata(13, "B601RDN", t) is False          # prima: retinuta
    assert plates.confirmata(13, "B601RDN", t + 10) is True      # a doua, in 30 s: confirmata
    assert plates.confirmata(13, "B601RDN", t + 20) is False     # consumata: o ia de la capat


def test_confirmata_expira_si_e_per_camera():
    plates._pending.clear()
    t = 2000.0
    plates.confirmata(13, "IF22JON", t)
    assert plates.confirmata(14, "IF22JON", t + 5) is False      # alta camera: nu conteaza
    assert plates.confirmata(13, "IF22JON", t + 31) is False     # expirata: iar prima
    assert plates.confirmata(13, "IF22JON", t + 40) is True


def test_stats_numara_pe_zile_si_necunoscute(tmp_path, monkeypatch):
    azi = time.strftime("%Y-%m-%d")
    ieri = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    demult = "2020-01-01"
    rows = [{"t": f"{azi} 10:00:00", "cam": 13, "numar": "B601RDN", "nume": "noi"},
            {"t": f"{azi} 11:00:00", "cam": 13, "numar": "XX11YYY"},
            {"t": f"{ieri} 09:00:00", "cam": 14, "numar": "XX11YYY"},
            {"t": f"{demult} 09:00:00", "cam": 14, "numar": "ZZ99ZZZ"}]
    monkeypatch.setattr(plates, "reads", lambda limit=100: rows)
    monkeypatch.setattr(plates, "known", lambda: {"B601RDN": "noi"})
    st = plates.stats()
    assert st["azi"] == 2 and st["total7"] == 3 and st["necunoscute7"] == 2
    assert [d["zi"] for d in st["zile"]] == [ieri, azi]


def _notify_cu(monkeypatch, cfg):
    trimise = []
    monkeypatch.setattr(notify.settings, "get", lambda k: cfg.get(k))
    monkeypatch.setattr(notify, "send", lambda text, jpeg=None, key=None, sync=False: trimise.append(text))
    monkeypatch.setattr(notify, "link", lambda cam, ts=None: "")
    monkeypatch.setattr(notify, "_cam_name", lambda c: f"cam{c}")
    return trimise


def test_alerta_masina_necunoscuta_doar_pe_camerele_alese(monkeypatch):
    cfg = {"lpr_notify_known": True, "lpr_notify_all": False,
           "lpr_notify_unknown": True, "lpr_alert_cams": [13, 14]}
    t = _notify_cu(monkeypatch, cfg)
    notify.plate(13, "XX11YYY")
    notify.plate(5, "XX11YYY")
    assert len(t) == 1 and "necunoscută" in t[0] and "XX11YYY" in t[0]
    cfg["lpr_alert_cams"] = []
    notify.plate(5, "XX11YYY")
    assert len(t) == 2                                           # gol = toate camerele
    cfg["lpr_notify_unknown"] = False
    notify.plate(13, "XX11YYY")
    assert len(t) == 2                                           # oprit: tacere
    notify.plate(13, "B601RDN", nume="noi")
    assert len(t) == 3 and "noi" in t[2]                         # cunoscutele raman


def _emb(seed):
    v = np.random.default_rng(seed).normal(size=512)
    return v / np.linalg.norm(v)


def test_cluster_grupeaza_fetele_asemanatoare():
    a, b = _emb(1), _emb(2)
    E = np.array([a, a * 0.9 + b * 0.1, b, b * 0.95 + a * 0.05])
    E = E / np.linalg.norm(E, axis=1, keepdims=True)
    cl = faces._cluster(E, 0.45)
    assert sorted(sorted(c) for c in cl) == [[0, 1], [2, 3]]


def test_unknown_groups_cu_sugestie_si_mostre(monkeypatch):
    stefan, strain = _emb(7), _emb(8)
    E = np.array([stefan] * 3 + [strain])
    meta = [("2026-10-20", "unknown/2026-10-20/a.jpg", "/x/a"),
            ("2026-10-21", "unknown/2026-10-21/b.jpg", "/x/b"),
            ("2026-10-22", "unknown/2026-10-22/c.jpg", "/x/c"),
            ("2026-10-22", "unknown/2026-10-22/d.jpg", "/x/d")]
    monkeypatch.setattr(faces, "_unknown_embeddings", lambda: (E, meta))
    monkeypatch.setattr(faces, "_load_embeddings", lambda: {"stefan": np.array([stefan])})
    g = faces.unknown_groups(sim_thr=0.45, min_size=2)
    assert len(g) == 1 and g[0]["n"] == 3 and g[0]["zile"] == 3
    assert g[0]["sugestie"]["nume"] == "stefan" and g[0]["sugestie"]["sim"] > 0.99
    assert g[0]["poze"] == [m[1] for m in meta[:3]] and len(g[0]["mostre"]) <= 6
    assert faces.unknown_groups(sim_thr=0.45, min_size=4) == []
