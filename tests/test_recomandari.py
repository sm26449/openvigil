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


def test_zona_fierbinte_din_candidatii_ingramaditi():
    import detect
    tufa = [(0.2, 0.8, 0.1, 0.1)] * 90 + [(0.3, 0.75, 0.1, 0.15)] * 60       # stanga-jos, doua celule vecine
    rest = [(x / 10, 0.3, 0.05, 0.05) for x in range(10)] * 5               # 50 raspanditi pe un rand
    drum = [(0.5, 0.2, 0.1, 0.1)] * 40                                       # masini ziua, in alta parte
    r = R.r_zona_fierbinte(14, None, tufa + rest, drum)
    assert r and r["actiune"]["tip"] == "zona" and r["dovezi"]["parte_pct"] == 75
    assert r["dovezi"]["petic"] == [0.25, 0.667, 0.375, 1.0] and "stânga-jos" in r["text"]
    pts = r["actiune"]["puncte"]
    assert len(pts) == 8 and [0, 0] in pts and [1, 1] in pts
    assert not detect._in_zone(pts, 0.3, 0.85) and detect._in_zone(pts, 0.5, 0.3) and detect._in_zone(pts, 0.3, 0.5)
    assert R.r_zona_fierbinte(14, [[0, 0], [1, 0], [1, 1], [0, 1]], tufa, drum) is None    # are deja zona
    assert R.r_zona_fierbinte(14, None, tufa[:50], drum) is None                             # prea putini
    peste_tot = [(x / 10, y / 6, 0.05, 0.05) for x in range(10) for y in range(6)] * 4
    assert R.r_zona_fierbinte(14, None, peste_tot, drum) is None                             # raspanditi
    # pe petic trec obiecte reale ziua (drumul plin de picaturi): ramane
    assert R.r_zona_fierbinte(14, None, tufa + rest, drum + [(0.25, 0.8, 0.1, 0.1)] * 10) is None
    # un petic secundar, mult mai cald decat restul: ajunge si un sfert din zgomot
    colt = [(0.9, 0.8, 0.05, 0.1)] * 120
    r = R.r_zona_fierbinte(13, None, colt + peste_tot[:240], drum)
    assert r and r["dovezi"]["petic"] == [0.875, 0.833, 1.0, 1.0]
    assert r["actiune"]["puncte"] == [[0, 0], [1, 0], [1, 1], [1, 0.833], [0.875, 0.833], [0.875, 1], [0, 1]]   # coltul, o data
    # celula cea mai calda e drumul (ploaie), dar ziua trec masini pe el: sarim la coltul urmator
    drum_ploaie = [(0.3, 0.6, 0.1, 0.1)] * 200
    r = R.r_zona_fierbinte(13, None, drum_ploaie + colt + peste_tot[:240], [(0.3, 0.6, 0.1, 0.1)] * 40)
    assert r and r["dovezi"]["petic"] == [0.875, 0.833, 1.0, 1.0] and r["dovezi"]["detectii_zi"] == 0


def test_poligon_fara_pe_fiecare_margine():
    import detect
    for (x1, y1, x2, y2), inauntru, afara in [((0.1, 0.7, 0.4, 0.95), (0.6, 0.5), (0.2, 0.9)),    # jos
                                              ((0.1, 0.05, 0.4, 0.3), (0.6, 0.5), (0.2, 0.1)),     # sus
                                              ((0.8, 0.4, 0.95, 0.7), (0.5, 0.5), (0.9, 0.5)),     # dreapta
                                              ((0.05, 0.4, 0.2, 0.7), (0.5, 0.5), (0.1, 0.5))]:    # stanga
        pts = R.poligon_fara(x1, y1, x2, y2)
        assert len(pts) == 8 and detect._in_zone(pts, *inauntru) and not detect._in_zone(pts, *afara)
        assert all(0 <= v <= 1 for p in pts for v in p)


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


def test_noapte_telegram_din_verdicte():
    def rand(i, inc, verdict="gresit", t="2026-11-01 03:1%d:00"):
        return {"id": f"n{i}", "verdict": verdict, "sursa_initiala": "yolo-world", "incredere": inc, "t": t % i}
    fb = {f"n{i}": rand(i, inc) for i, inc in enumerate((0.78, 0.74, 0.52, 0.73))}
    fb["ok1"] = rand(9, 0.9, "ok")
    r = R.r_noapte_telegram(6, fb, {}, _noapte, 0.5)
    assert r and r["actiune"] == {"tip": "detect", "scena_noapte": 0.83} and r["dovezi"]["respinse_noaptea"] == 4
    assert R.r_noapte_telegram(6, fb, {"scena_noapte": 0.8}, _noapte, 0.8) is None       # are deja prag
    assert R.r_noapte_telegram(6, dict(list(fb.items())[:2]), {}, _noapte, 0.5) is None   # prea putine
    zi = {k: {**v, "t": "2026-11-01 13:00:00"} for k, v in fb.items()}
    assert R.r_noapte_telegram(6, zi, {}, _noapte, 0.5) is None                           # ziua nu conteaza
    fb["v"] = {**rand(7, 0.3), "sursa_initiala": "vocabular"}
    assert R.r_noapte_telegram(6, fb, {}, _noapte, 0.5)["dovezi"]["total_noaptea"] == 5   # vocabularul nu intra


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
    assert apeluri[-1] == ("detect", 14, {"conf_noapte": 0.65, "scena_noapte": None})
    recs.append({"id": "noapte_telegram", "actiune": {"tip": "detect", "scena_noapte": 0.83}})
    R.aplica(14, "noapte_telegram")
    assert apeluri[-1] == ("detect", 14, {"conf_noapte": None, "scena_noapte": 0.83})
    R.aplica(14, "scena")
    assert stocat["scena_cams"] == [14] and stocat["track_cams"] == [3, 14]
    R.aplica(14, "vocabular")
    assert stocat["scena_vocabular"] == ["om", "cal cu căruță"]
    with pytest.raises(ValueError):
        R.aplica(14, "zgomot")
    with pytest.raises(ValueError):
        R.aplica(14, "nu_exista")
