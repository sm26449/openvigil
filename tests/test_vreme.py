"""Vremea (app/vreme.py): codurile Open-Meteo, estimarea din camere (contoare cumulative,
ceata din contrast), combinarea surselor, fisierul citit de nvr-ai, geocodarea (requests
inlocuit)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import vreme  # noqa: E402


def test_coduri_wmo():
    assert vreme.cod_stare(0) == "senin" and vreme.cod_stare(3) == "innorat"
    assert vreme.cod_stare(45) == "ceata" and vreme.cod_stare(61) == "ploaie" and vreme.cod_stare(81) == "ploaie"
    assert vreme.cod_stare(73) == "ninsoare" and vreme.cod_stare(96) == "furtuna"
    assert vreme.cod_stare(None) == "necunoscut" and vreme.cod_stare("x") == "necunoscut"


def test_din_camere_cu_contoare_cumulative(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    cv2 = pytest.importorskip("cv2")
    monkeypatch.setattr(vreme, "SCENA_DIR", str(tmp_path))
    (tmp_path / "14").mkdir()
    (tmp_path / "14" / "stare.json").write_text(json.dumps({"cadre": 1000, "cadre_zgomot": 400, "instabil_pct": 3.0}))
    rng = np.random.default_rng(1)
    cv2.imwrite(str(tmp_path / "14" / "fundal.jpg"), rng.integers(40, 220, (120, 160), dtype=np.uint8))
    prev = {14: (500, 100)}                         # de la citirea anterioara: 500 cadre noi, 300 zgomot
    d = vreme.din_camere([14], prev)
    assert d["camere"] == 1 and d["ploaie_pct"] == 60 and d["instabil_pct"] == 3.0
    assert d["ceata"] is False and prev[14] == (1000, 400)
    # ceata: fundal lăptos, fara contrast si fara margini
    cv2.imwrite(str(tmp_path / "14" / "fundal.jpg"), np.full((120, 160), 160, np.uint8))
    d2 = vreme.din_camere([14], prev)
    assert d2["ceata"] is True and d2["ploaie_pct"] is None   # fara cadre noi -> nu stim ploaia
    assert vreme.din_camere([99], {}) is None


def test_combina():
    ext = {"stare": "senin", "e_zi": True, "vant": False}
    assert vreme.combina(ext, None)["stare"] == "senin" and vreme.combina(ext, None)["sursa"] == "exterior"
    r = vreme.combina(ext, {"ploaie_pct": 45, "instabil_pct": 2, "ceata": False})
    assert (r["stare"], r["sursa"], r["rea"]) == ("ploaie", "ambele", True)      # camerele vad ploaia
    r = vreme.combina(None, {"ploaie_pct": 0, "instabil_pct": 15, "ceata": True, "luminozitate": 120})
    assert (r["stare"], r["sursa"], r["vant"]) == ("ceata", "camere", True)
    r = vreme.combina({"stare": "ploaie", "e_zi": False, "vant": True}, {"ploaie_pct": 0})
    assert r["stare"] == "ploaie" and r["noapte"] is True and r["vant"]
    assert vreme.combina(None, None)["stare"] == "necunoscut"


def test_fisier_si_stare_scurta(tmp_path, monkeypatch):
    monkeypatch.setattr(vreme, "FISIER", str(tmp_path / "vreme.json"))
    vreme._citit.update(t=0, d=None)
    assert vreme.citeste() == {}
    import time
    (tmp_path / "vreme.json").write_text(json.dumps({"stare": "ploaie", "rea": True, "t": time.time()}))
    vreme._citit.update(t=0, d=None)
    assert vreme.citeste()["rea"] is True
    (tmp_path / "vreme.json").write_text(json.dumps({"stare": "ploaie", "rea": True, "t": time.time() - 7200}))
    vreme._citit.update(t=0, d=None)
    assert vreme.citeste() == {}                      # prea veche
    vreme._stare.clear()
    vreme._stare.update({"stare": "necunoscut"})
    assert vreme.stare_scurta() is None
    vreme._stare.update({"stare": "ceata"})
    assert vreme.stare_scurta() == "ceata"
    vreme._stare.clear()


def test_geocodare_si_exterior(monkeypatch):
    import requests

    class R:
        def __init__(self, d):
            self.d = d

        def raise_for_status(self):
            pass

        def json(self):
            return self.d

    apel = {}

    def get(url, params=None, timeout=0):
        apel[url] = params
        if "geocoding" in url:
            return R({"results": [{"name": "Lipia", "country": "România", "admin1": "Buzău", "latitude": 45.26, "longitude": 26.84}]})
        return R({"current": {"time": "2026-10-31T23:00", "temperature_2m": 7.2, "is_day": 0, "precipitation": 0.4,
                              "weather_code": 61, "cloud_cover": 90, "wind_speed_10m": 12, "wind_gusts_10m": 44},
                  "hourly": {"time": ["2026-10-31T22:00", "2026-10-31T23:00"], "visibility": [8000, 600]}})
    monkeypatch.setattr(requests, "get", get)
    loc = vreme.cauta_localitate("Lipia")
    assert loc[0]["nume"] == "Lipia" and loc[0]["judet"] == "Buzău" and loc[0]["lat"] == 45.26
    assert vreme.cauta_localitate("L") == []
    e = vreme.exterior({"lat": 45.26, "lon": 26.84})
    assert e["stare"] == "ploaie" and e["vant"] is True and e["vizibilitate_m"] == 600 and e["e_zi"] is False
    assert apel[vreme.URL_VREME]["latitude"] == 45.26
    assert vreme.text({"stare": "ploaie", "vant": True, "exterior": e, "camere": {"ploaie_pct": 40}, "locatie": "Lipia"}).startswith("ploaie, 7°C, rafale 44 km/h, 40%")
