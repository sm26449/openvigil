#!/usr/bin/env python3
"""Vremea, din doua surse, ca detectia sa stie in ce conditii vede:

  1. de afara, prin Open-Meteo (gratuit, fara cheie): conditia curenta pentru coordonatele din
     Setari (`locatie_lat`/`locatie_lon`, gasite dupa numele localitatii prin geocodarea lor);
  2. din camere, fara internet: cadrele sarite ca zgomot de „Invata scena” (ploaie, ninsoare,
     granulatie), harta de instabilitate (vant), contrastul si claritatea fundalului (ceata).

Starea combinata se scrie in data/vreme.json la cateva minute (firul `start()` din nvr-web);
nvr-ai o citeste de acolo (`citeste()`), ca pragurile pe vreme sa nu depinda de retea. Pe
fiecare eveniment din jurnal intra campul `vreme` (starea scurta), deci cardurile, rezumatele,
antrenamentul si asistentul stiu ca „era ceata”.

Starile scurte: senin, innorat, ploaie, ninsoare, ceata, furtuna, necunoscut; separat, semnele
`vant` (rafale) si `noapte`.
"""
import json
import os
import threading
import time

import logare
import settings
import store
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FISIER = os.path.join(ROOT, "data", "vreme.json")
SCENA_DIR = os.path.join(ROOT, "data", "scena")
INTERVAL = 300             # secunde intre doua citiri (Open-Meteo cere politete; vremea nu fuge)
VANT_KMH = 40              # rafale de la care marcam „vant”
URL_VREME = "https://api.open-meteo.com/v1/forecast"
URL_GEO = "https://geocoding-api.open-meteo.com/v1/search"
REA = {"ploaie", "ninsoare", "ceata", "furtuna"}     # starile in care detectia e mai greu de crezut

_stare = {}
_lock = threading.Lock()
_fir = None
_prev_scena = {}           # cam -> (cadre, cadre_zgomot) la citirea anterioara (contoarele sunt cumulative)
_citit = {"t": 0.0, "d": None}


# ---- de afara ----
def cod_stare(cod):
    """Codul WMO al Open-Meteo -> starea scurta."""
    try:
        cod = int(cod)
    except (TypeError, ValueError):
        return "necunoscut"
    if cod == 0:
        return "senin"
    if cod in (1, 2, 3):
        return "innorat"
    if cod in (45, 48):
        return "ceata"
    if 51 <= cod <= 67 or 80 <= cod <= 82:
        return "ploaie"
    if 71 <= cod <= 77 or cod in (85, 86):
        return "ninsoare"
    if cod >= 95:
        return "furtuna"
    return "necunoscut"


def locatie():
    s = settings.load()
    try:
        lat, lon = float(s.get("locatie_lat")), float(s.get("locatie_lon"))
    except (TypeError, ValueError):
        return None
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    return {"lat": lat, "lon": lon, "nume": s.get("locatie_nume") or ""}


def cauta_localitate(nume, limba="ro"):
    """Localitatile care se potrivesc cu numele (geocodarea Open-Meteo): [{nume, tara, judet, lat, lon}]."""
    import requests
    nume = (nume or "").strip()
    if len(nume) < 2:
        return []
    r = requests.get(URL_GEO, params={"name": nume, "count": 8, "language": limba, "format": "json"}, timeout=15)
    r.raise_for_status()
    out = []
    for x in r.json().get("results") or []:
        out.append({"nume": x.get("name"), "tara": x.get("country"), "judet": x.get("admin1"),
                    "lat": round(float(x["latitude"]), 4), "lon": round(float(x["longitude"]), 4)})
    return out


def exterior(loc=None):
    """Conditia curenta de la Open-Meteo pentru locatia data (sau din Setari); None fara locatie."""
    import requests
    loc = loc or locatie()
    if not loc:
        return None
    r = requests.get(URL_VREME, params={
        "latitude": loc["lat"], "longitude": loc["lon"], "timezone": "auto",
        "current": "temperature_2m,is_day,precipitation,rain,snowfall,weather_code,cloud_cover,"
                   "wind_speed_10m,wind_gusts_10m",
        "hourly": "visibility", "daily": "sunrise,sunset", "forecast_days": 1}, timeout=15)
    r.raise_for_status()
    d = r.json()
    c = d.get("current") or {}
    viz = None
    try:
        ore = d["hourly"]["time"]
        acum = (c.get("time") or "")[:13]
        for i, h in enumerate(ore):
            if h[:13] == acum:
                viz = d["hourly"]["visibility"][i]
                break
    except (KeyError, IndexError, TypeError):
        pass
    cod = c.get("weather_code")
    stare = cod_stare(cod)
    if stare in ("senin", "innorat") and viz is not None and viz < 1000:
        stare = "ceata"
    zi = d.get("daily") or {}
    rasarit = (zi.get("sunrise") or [None])[0]
    apus = (zi.get("sunset") or [None])[0]
    return {"stare": stare, "cod": cod, "temperatura": c.get("temperature_2m"),
            "rasarit": rasarit[11:16] if rasarit else None, "apus": apus[11:16] if apus else None,
            "precipitatii_mm": c.get("precipitation"), "nori_pct": c.get("cloud_cover"),
            "vant_kmh": c.get("wind_speed_10m"), "rafale_kmh": c.get("wind_gusts_10m"),
            "vizibilitate_m": viz, "e_zi": bool(c.get("is_day")),
            "vant": (c.get("wind_gusts_10m") or 0) >= VANT_KMH, "ora": c.get("time")}


# ---- din camere ----
def _stare_scena(cam):
    try:
        with open(os.path.join(SCENA_DIR, str(cam), "stare.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _fundal_indicatori(cam):
    """(luminozitate 0..255, contrast = abaterea standard, claritate = varianta Laplace) din fundal.jpg."""
    try:
        import cv2
        img = cv2.imread(os.path.join(SCENA_DIR, str(cam), "fundal.jpg"), cv2.IMREAD_GRAYSCALE)
        if img is None:
            return None
        return float(img.mean()), float(img.std()), float(cv2.Laplacian(img, cv2.CV_64F).var())
    except Exception:
        return None


def din_camere(cams=None, prev=None):
    """Estimarea din camerele cu scena: {ploaie_pct, instabil_pct, ceata, luminozitate, camere}.
    ploaie_pct = cat din cadrele NOI (de la citirea anterioara) au fost sarite ca zgomot."""
    prev = _prev_scena if prev is None else prev
    cams = cams if cams is not None else [int(x) for x in (settings.get("scena_cams") or [])]
    zg, tot, inst, lum, contrast, clar, n = 0, 0, [], [], [], [], 0
    for cam in cams:
        st = _stare_scena(cam)
        if not st:
            continue
        c, z = int(st.get("cadre") or 0), int(st.get("cadre_zgomot") or 0)
        pc, pz = prev.get(cam, (c, z))
        if c >= pc:
            tot += c - pc
            zg += max(0, z - pz)
        prev[cam] = (c, z)
        inst.append(float(st.get("instabil_pct") or 0))
        ind = _fundal_indicatori(cam)
        if ind:
            lum.append(ind[0])
            contrast.append(ind[1])
            clar.append(ind[2])
        n += 1
    if not n:
        return None
    med = lambda v: round(sum(v) / len(v), 1) if v else None  # noqa: E731
    out = {"camere": n, "ploaie_pct": round(100 * zg / tot) if tot >= 50 else None,
           "instabil_pct": med(inst), "luminozitate": med(lum), "contrast": med(contrast), "claritate": med(clar)}
    # ceata: cadru lăptos — contrast mic si claritate mica, dar nu intuneric (noaptea IR e altceva)
    out["ceata"] = bool(contrast and clar and out["luminozitate"] and out["luminozitate"] > 70
                        and out["contrast"] < 22 and out["claritate"] < 40)
    return out


# ---- combinarea ----
def combina(ext, cam, noapte=None):
    """Starea scurta + semnele, din ce avem: afara primeaza; camerele corecteaza ploaia/ceata."""
    stare, sursa = "necunoscut", "-"
    if ext:
        stare, sursa = ext["stare"], "exterior"
    if cam:
        if (cam.get("ploaie_pct") or 0) >= 30 and stare not in ("ploaie", "ninsoare", "furtuna"):
            stare, sursa = "ploaie", "camere" if not ext else "ambele"
        elif cam.get("ceata") and stare in ("senin", "innorat", "necunoscut"):
            stare, sursa = "ceata", "camere" if not ext else "ambele"
        elif not ext:
            stare, sursa = ("innorat" if (cam.get("luminozitate") or 0) > 70 else "necunoscut"), "camere"
    if noapte is None:
        noapte = (not ext["e_zi"]) if ext else None
    vant = bool((ext or {}).get("vant")) or (cam or {}).get("instabil_pct", 0) >= 12
    return {"stare": stare, "sursa": sursa, "vant": vant, "noapte": noapte, "rea": stare in REA}


def actualizeaza():
    """O citire completa -> data/vreme.json; intoarce starea."""
    ext = None
    if locatie():
        try:
            ext = exterior()
        except Exception as e:
            logare.info(f"[vreme] open-meteo: {str(e)[:80]}")
    cam = None
    try:
        cam = din_camere()
    except Exception as e:
        logare.info(f"[vreme] camere: {str(e)[:80]}")
    try:
        import rules
        noapte = rules._is_night()
    except Exception:
        noapte = None
    d = {**combina(ext, cam, noapte), "exterior": ext, "camere": cam, "t": time.time(),
         "locatie": (locatie() or {}).get("nume") or None}
    with _lock:
        _stare.clear()
        _stare.update(d)
    try:
        store.atomic_write_json(FISIER, d)
    except OSError as e:
        logare.info(f"[vreme] scriere: {str(e)[:60]}")
    return d


def citeste(maxim_vechi=INTERVAL * 3):
    """Starea din data/vreme.json (pentru nvr-ai si pentru cine nu are firul), cache 60 s;
    {} daca lipseste sau e prea veche."""
    now = time.time()
    if now - _citit["t"] < 60 and _citit["d"] is not None:
        return _citit["d"]
    d = {}
    try:
        with open(FISIER, encoding="utf-8") as f:
            d = json.load(f)
        if now - float(d.get("t") or 0) > maxim_vechi:
            d = {}
    except (OSError, ValueError):
        d = {}
    _citit.update(t=now, d=d)
    return d


def acum():
    """Starea curenta: din memorie (nvr-web) sau din fisier."""
    with _lock:
        if _stare:
            return dict(_stare)
    return citeste()


def stare_scurta():
    """„ploaie”, „senin”… sau None cand nu stim — campul `vreme` din evenimente."""
    s = acum().get("stare")
    return s if s and s != "necunoscut" else None


def text(d=None):
    """Pentru om: „ploaie, 7°C, rafale 45 km/h (Sinaia)”."""
    d = d or acum()
    if not d:
        return t("vreme necunoscută")
    nume = {"senin": t("senin"), "innorat": t("înnorat"), "ploaie": t("ploaie"), "ninsoare": t("ninsoare"),
            "ceata": t("ceață"), "furtuna": t("furtună"), "necunoscut": t("vreme necunoscută")}
    parti = [nume.get(d.get("stare"), str(d.get("stare")))]
    e = d.get("exterior") or {}
    if e.get("temperatura") is not None:
        parti.append(f"{round(e['temperatura'])}°C")
    if d.get("vant"):
        parti.append(t("rafale {v} km/h", v=round(e.get("rafale_kmh") or 0)) if e.get("rafale_kmh") else t("vânt"))
    c = d.get("camere") or {}
    if c.get("ploaie_pct"):
        parti.append(t("{pct}% cadre de ploaie pe camere", pct=c["ploaie_pct"]))
    s = ", ".join(parti)
    return f"{s} ({d['locatie']})" if d.get("locatie") else s


def _bucla():
    while True:
        try:
            actualizeaza()
        except Exception as e:
            logare.info(f"[vreme] {str(e)[:80]}")
        time.sleep(INTERVAL)


def start():
    """Firul din nvr-web: la fiecare INTERVAL secunde, o citire (afara + camere)."""
    global _fir
    if _fir is None:
        _fir = threading.Thread(target=_bucla, daemon=True, name="vreme")
        _fir.start()
