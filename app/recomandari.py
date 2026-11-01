#!/usr/bin/env python3
"""Recomandari per camera („Invata scena”, faza B): ce ar imbunatati detectia pe camera asta,
deduse din ce a vazut sistemul (jurnal, candidati, verdictele omului, harta de instabilitate)
si aplicabile cu un clic, pe butoanele care exista deja (zona de detectie, detectie pe
regiuni HD, urmarire, scena, vocabular, prag de noapte).

Fiecare recomandare: {id, tip, titlu, text, dovezi, actiune}; `actiune` None = doar
informativa. Regulile sunt pure (primesc datele, intorc recomandari) ca sa fie testabile;
`pentru_camera` le aduna datele, `aplica` executa actiunea unei recomandari recalculate
(nu luam actiuni din cerere: clientul trimite doar id-ul).
"""
import json
import os
import statistics

import logare
import settings
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENA_DIR = os.path.join(ROOT, "data", "scena")

ARIE_MICA = 0.005          # sub 0,5% din cadru: obiecte mici, la distanta -> regiuni HD
MIN_ESANTION = 30          # cate chenare ca sa credem mediana
INSTABIL_MIN = 0.05        # fractia de cadru instabila de la care propunem o zona
INSTABIL_CONCENTRAT = 0.8  # cat din instabilitate trebuie sa stea in banda de sus propusa
FIERBINTE_GRILA = (6, 8)   # randuri x coloane in care numaram centrele candidatilor scenei
FIERBINTE_MIN = 100        # cati candidati ca sa credem ca un petic e „fierbinte”
FIERBINTE_PARTE = 0.15     # cat din candidatii-zgomot trebuie sa stea in petic
FIERBINTE_SEMINTE = 5      # incercam peticul din fiecare dintre cele mai calde celule, in ordine
FIERBINTE_CELULE = 12      # peticul: cel mult atatea celule (un sfert de cadru), altfel e raspandit
FIERBINTE_CRESTERE = 0.2   # peticul creste cu o celula vecina doar daca ea are macar atat din varf
FIERBINTE_DENSITATE = 4    # peticul trebuie sa fie de atatea ori mai des decat media cadrului
FIERBINTE_DETECTII = 0.03  # peste atat din detectiile de zi in petic = trec obiecte reale, nu-l scoatem
FIERBINTE_ZILE = 3
PRAG_NOAPTE = 0.65
MIN_FALS_NOAPTE = 3
MIN_DETECTII_SCENA = 50
MIN_VOCAB = 2
OM = {"person", "om", "copil"}


# ---- regulile (pure) ----
def r_regiuni_hd(cam, chenare, tile_cams):
    """Obiecte mici la distanta -> detectie pe regiuni din cadrul HD (tile_cams)."""
    if cam in tile_cams or len(chenare) < MIN_ESANTION:
        return None
    arii = sorted(b[2] * b[3] for b in chenare)
    med = statistics.median(arii)
    if med >= ARIE_MICA:
        return None
    return {"id": "regiuni_hd", "tip": "rezolutie",
            "titlu": t("Detecție pe regiuni din cadrul HD"),
            "text": t("Jumătate din obiectele văzute aici au sub {pct}% din cadru: pe fluxul mic un om la distanță are câțiva pixeli. Cu detecția pe regiuni, zona cu mișcare se decupează din HD și se inferează separat.",
                      pct=round(med * 100, 2)),
            "dovezi": {"chenare": len(chenare), "arie_mediana_pct": round(med * 100, 3)},
            "actiune": {"tip": "setari_lista", "cheie": "tile_cams", "adauga": cam}}


def r_zona_instabila(cam, zona, instabil):
    """Harta de instabilitate concentrata in banda de sus (copaci, cer) -> zona de detectie
    care o lasa afara. `instabil` = (fractie_total, profil_pe_randuri[0..1])."""
    if zona or not instabil:
        return None
    total, profil = instabil
    if total < INSTABIL_MIN or not profil:
        return None
    suma = sum(profil)
    if suma <= 0:
        return None
    acum, f = 0.0, None
    for i, v in enumerate(profil):
        acum += v
        if acum >= INSTABIL_CONCENTRAT * suma:
            f = (i + 1) / len(profil)
            break
    if f is None or f > 0.5:
        return None
    f = round(f + 0.02, 2)
    return {"id": "zona_instabila", "tip": "zona",
            "titlu": t("Zonă de detecție sub banda instabilă"),
            "text": t("{pct}% din cadru se mișcă tot timpul (frunze, apă, cer), aproape tot în partea de sus ({sus}%). O zonă de detecție de la {sus}% în jos taie fals-pozitivele de acolo fără să piardă drumul sau curtea.",
                      pct=round(total * 100, 1), sus=round(f * 100)),
            "dovezi": {"instabil_pct": round(total * 100, 1), "banda_sus": f},
            "actiune": {"tip": "zona", "puncte": [[0, f], [1, f], [1, 1], [0, 1]]}}


def _decupaj_jos(x1, y1, x2):
    """Poligonul cadrului intreg din care lipseste dreptunghiul (x1, y1)-(x2, 1), lipit de jos."""
    return [[0, 0], [1, 0], [1, 1], [x2, 1], [x2, y1], [x1, y1], [x1, 1], [0, 1]]


def poligon_fara(x1, y1, x2, y2):
    """Zona de detectie = tot cadrul minus dreptunghiul normalizat, prelungit pana la marginea
    cea mai apropiata (un poligon simplu, in forma de U; editorul de zona il arata si il
    poate retusa). Coordonatele sunt rotunjite la 3 zecimale."""
    marg = min(("jos", 1 - y2), ("sus", y1), ("stanga", x1), ("dreapta", 1 - x2), key=lambda m: m[1])[0]
    if marg == "jos":
        pts = _decupaj_jos(x1, y1, x2)
    elif marg == "sus":
        pts = [[x, 1 - y] for x, y in _decupaj_jos(x1, 1 - y2, x2)]
    elif marg == "dreapta":
        pts = [[y, x] for x, y in _decupaj_jos(y1, x1, y2)]
    else:
        pts = [[1 - y, x] for x, y in _decupaj_jos(y1, 1 - x2, y2)]
    pts = [[round(float(x), 3), round(float(y), 3)] for x, y in pts]
    return [p for i, p in enumerate(pts) if p != pts[i - 1]]      # un petic din colt ar repeta coltul


def _unde(x1, y1, x2, y2):
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    v = t("sus") if cy < 1 / 3 else t("jos") if cy > 2 / 3 else t("mijloc")
    o = t("stânga") if cx < 1 / 3 else t("dreapta") if cx > 2 / 3 else t("centru")
    return f"{o}-{v}"


def _zgomot(e):
    """Candidat fara obiect real: nimic recunoscut, un nume neutru sau doar o potrivire slaba de vocabular."""
    import scena_clasa
    return (e.get("eticheta") or "necunoscut") in scena_clasa.NEUTRE or e.get("sursa") in ("vocabular", "yolo-nesigur", "-", None)


def _celula(x, y, w, h):
    rr, cc = FIERBINTE_GRILA
    return min(int((y + h / 2) * rr), rr - 1), min(int((x + w / 2) * cc), cc - 1)


def r_zona_fierbinte(cam, zona, regiuni, detectii_zi):
    """Candidatii-zgomot ai scenei ingramaditi intr-un petic mic al cadrului (o tufa in vant, o
    creanga in IR, granulatie pe gazon) -> zona de detectie care lasa peticul afara.
    `regiuni` = chenarele normalizate (x, y, w, h) ale candidatilor fara obiect real din ultimele
    zile; `detectii_zi` = chenarele detectiilor YOLO de peste zi: daca pe petic trec masini sau
    oameni ziua, nu-l scoatem (drumul ramane drum, chiar daca ploaia il umple de candidati)."""
    if zona or len(regiuni) < FIERBINTE_MIN:
        return None
    rr, cc = FIERBINTE_GRILA
    grila = {}
    for x, y, w, h in regiuni:
        k = _celula(x, y, w, h)
        grila[k] = grila.get(k, 0) + 1
    suma = lambda ra, rb, ca, cb: sum(grila.get((r, c), 0) for r in range(ra, rb) for c in range(ca, cb))  # noqa: E731
    seminte = sorted(grila.items(), key=lambda kv: -kv[1])[:FIERBINTE_SEMINTE]
    for (r0, c0), varf in seminte:
        petic = _petic(grila, suma, r0, c0, varf, len(regiuni), detectii_zi)
        if petic:
            return petic
    return None


def _petic(grila, suma, r0, c0, varf, total, detectii_zi):
    """Peticul crescut din celula (r0, c0), daca merita scos; altfel None."""
    rr, cc = FIERBINTE_GRILA
    r1, r2, c1, c2 = r0, r0 + 1, c0, c0 + 1          # peticul creste din celula calda, vecin cu vecin
    while True:
        pasi = [(suma(r1 - 1, r1, c1, c2), (r1 - 1, r2, c1, c2)) if r1 > 0 else (0, None),
                (suma(r2, r2 + 1, c1, c2), (r1, r2 + 1, c1, c2)) if r2 < rr else (0, None),
                (suma(r1, r2, c1 - 1, c1), (r1, r2, c1 - 1, c2)) if c1 > 0 else (0, None),
                (suma(r1, r2, c2, c2 + 1), (r1, r2, c1, c2 + 1)) if c2 < cc else (0, None)]
        castig, cadru = max(pasi, key=lambda p: p[0])
        if not cadru or castig < FIERBINTE_CRESTERE * varf:
            break
        nr1, nr2, nc1, nc2 = cadru
        if (nr2 - nr1) * (nc2 - nc1) > FIERBINTE_CELULE:
            break
        r1, r2, c1, c2 = cadru
    in_petic = suma(r1, r2, c1, c2)
    celule = (r2 - r1) * (c2 - c1)
    if in_petic < FIERBINTE_MIN or in_petic < FIERBINTE_PARTE * total:
        return None
    if in_petic / celule < FIERBINTE_DENSITATE * total / (rr * cc):
        return None                                  # nu e mult mai cald decat restul cadrului
    x1, y1, x2, y2 = c1 / cc, r1 / rr, c2 / cc, r2 / rr
    reale = sum(1 for x, y, w, h in detectii_zi if x1 <= x + w / 2 < x2 and y1 <= y + h / 2 < y2)
    if reale > FIERBINTE_DETECTII * max(len(detectii_zi), 1) and reale >= 3:
        return None                                  # ziua trec obiecte reale pe acolo
    parte = round(100 * in_petic / total)
    arie = round(100 * (x2 - x1) * (y2 - y1))
    return {"id": "zona_fierbinte", "tip": "zona",
            "titlu": t("Zonă de detecție fără peticul care se mișcă mereu"),
            "text": t("{n} candidați fără obiect real în ultimele {zile} zile, {parte}% din ei într-un petic de {arie}% din cadru ({unde}), pe unde ziua nu trec obiecte: o tufă în vânt, o creangă în infraroșu, granulație pe gazon. O zonă de detecție care îl lasă afară îi oprește înainte de clasificare (și eliberează GPU-ul); zona se poate retușa din fișa camerei.",
                      n=total, zile=FIERBINTE_ZILE, parte=parte, arie=arie, unde=_unde(x1, y1, x2, y2)),
            "dovezi": {"candidati": total, "parte_pct": parte, "detectii_zi": reale,
                       "petic": [round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3)]},
            "actiune": {"tip": "zona", "puncte": poligon_fara(x1, y1, x2, y2)}}


def r_prag_noapte(cam, feedback, cfg, e_noapte):
    """Omul a respins „om” noaptea de mai multe ori -> prag de incredere mai mare noaptea."""
    if cfg.get("conf_noapte"):
        return None
    noapte = [r for r in feedback.values()
              if (r.get("eticheta_initiala") or "") in OM and e_noapte((r.get("t") or "")[11:16])]
    false = [r for r in noapte if r.get("verdict") == "gresit"]
    if len(false) < MIN_FALS_NOAPTE or len(false) < 0.5 * len(noapte):
        return None
    return {"id": "prag_noapte", "tip": "prag",
            "titlu": t("Prag de încredere mai mare noaptea"),
            "text": t("Ai respins {n} din {total} „om” văzuți noaptea pe camera asta. Un prag de {prag} noaptea (față de 0,5 ziua) lasă afară siluetele nesigure din IR; cele clare trec oricum.",
                      n=len(false), total=len(noapte), prag=PRAG_NOAPTE),
            "dovezi": {"respinse_noaptea": len(false), "total_noaptea": len(noapte)},
            "actiune": {"tip": "detect", "conf_noapte": PRAG_NOAPTE}}


def r_scena(cam, n_detectii, track_cams, scena_cams):
    if cam in scena_cams or cam not in track_cams or n_detectii < MIN_DETECTII_SCENA:
        return None
    return {"id": "scena", "tip": "scena",
            "titlu": t("Pornește „Învață scena”"),
            "text": t("Camera are {n} detecții recente și e urmărită, dar nu-și învață fundalul: cu scena pornită, ce e nou apare în fișă cu etichetă propusă și poți corecta cu un clic.", n=n_detectii),
            "dovezi": {"detectii": n_detectii},
            "actiune": {"tip": "scena", "activ": True}}


def r_urmarire(cam, detect_on, n_detectii, track_cams):
    if cam in track_cams or not detect_on or n_detectii < MIN_DETECTII_SCENA:
        return None
    return {"id": "urmarire", "tip": "urmarire",
            "titlu": t("Pornește urmărirea obiectelor"),
            "text": t("Detecția e pornită, dar fără urmărire fiecare obiect e o detecție la fiecare cadru; cu urmărire devine un episod cu durată și direcție, iar scena și numărarea au de unde lua cadrele."),
            "dovezi": {"detectii": n_detectii},
            "actiune": {"tip": "setari_lista", "cheie": "track_cams", "adauga": cam}}


def r_vocabular(cam, feedback, vocabular):
    """Numele scrise de om la „altceva…” care nu sunt in vocabular -> le adaugam."""
    import scena_clasa
    voc = {v.lower() for v in vocabular} | scena_clasa.NEUTRE      # „nimic”, „copac”… sunt verdicte, nu nume
    numar = {}
    for r in feedback.values():
        e = (r.get("eticheta") or "").strip()
        if r.get("verdict") == "gresit" and e and e.lower() not in voc:
            numar[e] = numar.get(e, 0) + 1
    noi = sorted(e for e, n in numar.items() if n >= MIN_VOCAB)
    if not noi:
        return None
    return {"id": "vocabular", "tip": "vocabular",
            "titlu": t("Nume noi în vocabular"),
            "text": t("Ai scris de mai multe ori nume care nu sunt în vocabular: {lista}. Odată adăugate, SigLIP și YOLO-World le caută singure.", lista=", ".join(noi)),
            "dovezi": {"nume": noi},
            "actiune": {"tip": "setari_lista", "cheie": "scena_vocabular", "adauga": noi}}


def r_zgomot(cam, stare):
    cadre, zg = stare.get("cadre") or 0, stare.get("cadre_zgomot") or 0
    if cadre < 500 or zg < 0.3 * cadre:
        return None
    return {"id": "zgomot", "tip": "info",
            "titlu": t("Multe cadre de ploaie sau granulație IR"),
            "text": t("{pct}% din cadrele scenei au fost sărite ca zgomot (ploaie, zăpadă, granulație). E normal pe vreme rea; candidații de noapte de aici sunt în mare parte picături, nu intruși.", pct=round(100 * zg / cadre)),
            "dovezi": {"cadre": cadre, "zgomot": zg}, "actiune": None}


# ---- datele ----
def _chenare(cam):
    """Chenarele obiectelor reale vazute pe camera: detectii (jurnal) + candidati clasificati."""
    import jurnal
    out = []
    for e in jurnal.query(limit=3000, tip="detectie", cam=cam):
        c = e.get("chenar")
        if isinstance(c, list) and len(c) == 4:
            out.append(tuple(float(x) for x in c))
    return out


def _regiuni(cam):
    """Chenarele normalizate (x, y, w, h) ale candidatilor-zgomot ai scenei din ultimele FIERBINTE_ZILE zile."""
    import time
    import jurnal
    out = []
    for e in jurnal.query(limit=5000, tip="scena_candidat", cam=cam, since=time.time() - FIERBINTE_ZILE * 86400):
        r = e.get("regiune")
        if isinstance(r, list) and len(r) == 4 and _zgomot(e):
            out.append(tuple(float(x) for x in r))
    return out


def _chenare_zi(cam):
    """Chenarele detectiilor de peste zi (orele de noapte din Setari): obiectele reale ale camerei.
    Noaptea o creanga in IR trece drept „om” si ar apara tocmai peticul pe care vrem sa-l scoatem."""
    import time
    import jurnal
    import rules
    out = []
    for e in jurnal.query(limit=3000, tip="detectie", cam=cam, since=time.time() - FIERBINTE_ZILE * 86400):
        c = e.get("chenar")
        if isinstance(c, list) and len(c) == 4 and not rules._is_night(str(e.get("t") or "")[11:16] or "12:00"):
            out.append(tuple(float(x) for x in c))
    return out


def _n_detectii(cam):
    import jurnal
    return len(jurnal.query(limit=3000, tip="detectie", cam=cam))


def _instabil(cam):
    """(fractie instabila, profil pe 20 de benzi orizontale) din data/scena/<cam>/instabil.png."""
    cale = os.path.join(SCENA_DIR, str(cam), "instabil.png")
    try:
        import numpy as np
        from PIL import Image
        a = np.asarray(Image.open(cale).convert("L")) > 0
    except Exception:
        return None
    if a.size == 0:
        return None
    total = float(a.mean())
    benzi = np.array_split(a, 20, axis=0)
    return total, [float(b.sum()) for b in benzi]


def _stare(cam):
    try:
        with open(os.path.join(SCENA_DIR, str(cam), "stare.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def pentru_camera(cam):
    """Toate recomandarile camerei, in ordinea importantei; niciodata nu arunca."""
    import detect
    import rules
    import scena_web
    import scena_clasa
    cam = int(cam)
    s = settings.load()
    lst = lambda k: {int(x) for x in (s.get(k) or [])}  # noqa: E731
    cfg = detect.cam_cfg(cam)
    out = []
    pasi = [
        lambda: r_prag_noapte(cam, scena_web.feedback_citeste(cam), cfg, rules._is_night),
        lambda: r_zona_fierbinte(cam, cfg.get("zone"), _regiuni(cam), _chenare_zi(cam)),
        lambda: r_zona_instabila(cam, cfg.get("zone"), _instabil(cam)),
        lambda: r_regiuni_hd(cam, _chenare(cam), lst("tile_cams")),
        lambda: r_urmarire(cam, bool(cfg.get("on")), _n_detectii(cam), lst("track_cams")),
        lambda: r_scena(cam, _n_detectii(cam), lst("track_cams"), lst("scena_cams")),
        lambda: r_vocabular(cam, scena_web.feedback_citeste(cam), scena_clasa.vocabular()),
        lambda: r_zgomot(cam, _stare(cam)),
    ]
    for pas in pasi:
        try:
            r = pas()
        except Exception as e:
            logare.info(f"[recomandari] cam{cam}: {str(e)[:80]}")
            r = None
        if r:
            out.append(r)
    return out


def aplica(cam, rid):
    """Executa actiunea recomandarii `rid` (recalculata acum). Intoarce recomandarea
    aplicata sau ridica ValueError."""
    cam = int(cam)
    r = next((x for x in pentru_camera(cam) if x["id"] == rid), None)
    if not r or not r.get("actiune"):
        raise ValueError(t("recomandarea nu mai e valabilă"))
    a = r["actiune"]
    if a["tip"] == "setari_lista":
        cur = list(settings.get(a["cheie"]) or [])
        noi = a["adauga"] if isinstance(a["adauga"], list) else [a["adauga"]]
        for v in noi:
            if v not in cur:
                cur.append(v)
        settings.update({a["cheie"]: cur})
    elif a["tip"] == "zona":
        import detect
        detect.set_zone(cam, a["puncte"])
    elif a["tip"] == "detect":
        import detect
        detect.set_detect(cam, conf_noapte=a.get("conf_noapte"))
    elif a["tip"] == "scena":
        sc = {int(x) for x in (settings.get("scena_cams") or [])}
        tr = {int(x) for x in (settings.get("track_cams") or [])}
        sc.add(cam)
        tr.add(cam)
        settings.update({"scena_cams": sorted(sc), "track_cams": sorted(tr)})
    else:
        raise ValueError(t("acțiune necunoscută"))
    return r
