"""Recomandarile per camera (app/recomandari.py): regulile pure, cu date sintetice, si
aplicarea unei recomandari (setari / zona / prag de noapte), cu detect si settings inlocuite."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import recomandari as R  # noqa: E402


def _noapte(hhmm):
    return hhmm >= "22:00" or hhmm < "06:00"


def test_regiuni_hd_doar_pentru_obiecte_mici():
    mici = [(0.5, 0.5, 0.04, 0.05)] * 40            # 0,2% din cadru
    mari = [(0.5, 0.5, 0.2, 0.3)] * 40
    r = R.r_regiuni_hd(14, mici, set())
    assert r and r["actiune"] == {"tip": "setari_lista", "cheie": "tile_cams", "adauga": 14}
    assert R.r_regiuni_hd(14, mari, set()) is None
    assert R.r_regiuni_hd(14, mici, {14}) is None                 # deja pornita
    assert R.r_regiuni_hd(14, mici[:10], set()) is None           # esantion mic


def test_zona_instabila_din_banda_de_sus():
    profil = [100] * 6 + [1] * 14                                 # instabil in primele 30%
    r = R.r_zona_instabila(13, None, (0.12, profil))
    assert r and r["actiune"]["tip"] == "zona"
    f = r["actiune"]["puncte"][0][1]
    assert 0.25 <= f <= 0.35 and r["actiune"]["puncte"][2] == [1, 1]     # 5 benzi din 20 tin 80%
    assert R.r_zona_instabila(13, [[0, 0.3], [1, 0.3], [1, 1], [0, 1]], (0.12, profil)) is None  # are zona
    assert R.r_zona_instabila(13, None, (0.02, profil)) is None    # prea putin instabil
    assert R.r_zona_instabila(13, None, (0.2, [5] * 20)) is None   # raspandit peste tot, nu banda


def test_prag_noapte_din_verdicte():
    fb = {f"a{i}": {"eticheta_initiala": "person", "verdict": "gresit", "t": "2026-10-31 23:1%d:00" % i} for i in range(4)}
    fb["ok1"] = {"eticheta_initiala": "person", "verdict": "ok", "t": "2026-10-31 02:00:00"}
    fb["zi"] = {"eticheta_initiala": "person", "verdict": "gresit", "t": "2026-10-31 12:00:00"}
    r = R.r_prag_noapte(6, fb, {}, _noapte)
    assert r and r["actiune"] == {"tip": "detect", "conf_noapte": R.PRAG_NOAPTE}
    assert r["dovezi"] == {"respinse_noaptea": 4, "total_noaptea": 5}
    assert R.r_prag_noapte(6, fb, {"conf_noapte": 0.65}, _noapte) is None       # deja setat
    putine = {k: v for k, v in fb.items() if k in ("a0", "a1")}
    assert R.r_prag_noapte(6, putine, {}, _noapte) is None                      # sub minim


def test_scena_urmarire_vocabular_zgomot():
    assert R.r_scena(5, 80, {5}, set())["actiune"] == {"tip": "scena", "activ": True}
    assert R.r_scena(5, 80, set(), set()) is None                 # intai urmarirea
    assert R.r_scena(5, 10, {5}, set()) is None
    assert R.r_urmarire(5, True, 80, set())["actiune"]["cheie"] == "track_cams"
    assert R.r_urmarire(5, False, 80, set()) is None
    fb = {"a": {"verdict": "gresit", "eticheta": "cal cu căruță"}, "b": {"verdict": "gresit", "eticheta": "cal cu căruță"},
          "c": {"verdict": "gresit", "eticheta": "om"}, "d": {"verdict": "gresit", "eticheta": "zimbru"}}
    r = R.r_vocabular(5, fb, ["om", "pisică"])
    assert r["actiune"]["adauga"] == ["cal cu căruță"]            # „zimbru” o singura data, „om” exista
    assert R.r_vocabular(5, {}, ["om"]) is None
    assert R.r_zgomot(5, {"cadre": 1000, "cadre_zgomot": 400})["actiune"] is None
    assert R.r_zgomot(5, {"cadre": 1000, "cadre_zgomot": 50}) is None


def test_aplica(monkeypatch):
    stocat = {"tile_cams": [3], "scena_vocabular": ["om"], "scena_cams": [], "track_cams": [3]}
    monkeypatch.setattr(R.settings, "get", lambda k: stocat.get(k))
    monkeypatch.setattr(R.settings, "update", lambda d: stocat.update(d))
    apeluri = []
    import detect
    monkeypatch.setattr(detect, "set_zone", lambda cam, pts: apeluri.append(("zona", cam, pts)))
    monkeypatch.setattr(detect, "set_detect", lambda cam, **k: apeluri.append(("detect", cam, k)))
    recs = [{"id": "regiuni_hd", "actiune": {"tip": "setari_lista", "cheie": "tile_cams", "adauga": 14}},
            {"id": "zona_instabila", "actiune": {"tip": "zona", "puncte": [[0, 0.3], [1, 0.3], [1, 1], [0, 1]]}},
            {"id": "prag_noapte", "actiune": {"tip": "detect", "conf_noapte": 0.65}},
            {"id": "scena", "actiune": {"tip": "scena", "activ": True}},
            {"id": "vocabular", "actiune": {"tip": "setari_lista", "cheie": "scena_vocabular", "adauga": ["cal cu căruță"]}},
            {"id": "zgomot", "actiune": None}]
    monkeypatch.setattr(R, "pentru_camera", lambda cam: recs)
    R.aplica(14, "regiuni_hd")
    assert stocat["tile_cams"] == [3, 14]
    R.aplica(14, "regiuni_hd")
    assert stocat["tile_cams"] == [3, 14]                          # idempotent
    R.aplica(14, "zona_instabila")
    assert apeluri[-1][0] == "zona" and apeluri[-1][2][0] == [0, 0.3]
    R.aplica(14, "prag_noapte")
    assert apeluri[-1] == ("detect", 14, {"conf_noapte": 0.65})
    R.aplica(14, "scena")
    assert stocat["scena_cams"] == [14] and stocat["track_cams"] == [3, 14]
    R.aplica(14, "vocabular")
    assert stocat["scena_vocabular"] == ["om", "cal cu căruță"]
    with pytest.raises(ValueError):
        R.aplica(14, "zgomot")
    with pytest.raises(ValueError):
        R.aplica(14, "nu_exista")
