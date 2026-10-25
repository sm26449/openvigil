#!/usr/bin/env python3
"""Motorul de reguli + jurnalul permanent de evenimente.

Toate evenimentele sistemului (detectie, persoana recunoscuta/necunoscuta, numar de
inmatriculare, alarma de camera, zgomot, camera cazuta...) trec printr-un singur punct:
hooks.emit(). De acolo ajung si aici — intai in jurnalul permanent (data/events.db, SQLite,
vezi jurnal.py; istoricul "ce s-a intamplat si cand", descarcabil ca CSV), apoi prin reguli.

O regula = declansator + conditii + actiuni:
  {"id": "...", "nume": "sirena noaptea la om necunoscut", "activ": true,
   "declansator": {"tip": "persoana_necunoscuta", "valoare": ""},
   "camere": [101, 102],            # gol/absent = toate camerele
   "interval": ["23:00", "06:00"],  # null = oricand; suporta trecerea peste miezul noptii
   "cooldown": 120,                 # secunde intre doua declansari ale ACELEIASI reguli
   "actiuni": [{"tip": "sirena", "sunet": 1, "volum": 90, "repetari": 3},
               {"tip": "strobo", "secunde": 30},
               {"tip": "anunt", "text": "Buna seara"},
               {"tip": "releu", "port": 1, "secunde": 10},
               {"tip": "notificare", "text": ""},
               {"tip": "webhook"}]}

Semantica lui "valoare" dupa tipul declansatorului:
  detectie:  eticheta ("person", "dog"...) sau gol = orice
  obiect:    eticheta, sau "biciclist" (om urmarit cu viteza de bicicleta: >= 2,2 inaltimi/s)
  persoana:  numele ("ion") sau gol = orice persoana RECUNOSCUTA
  persoana_necunoscuta: fara valoare (fata gasita, dar nu e in biblioteca)
  numar:     placuta exacta ("B123XYZ"), "cunoscut", "necunoscut" sau gol = orice
  alarma_camera: subtipul (VMD, linedetection, fielddetection...) sau gol = orice
  trecere:   "linie", "linie:intra", "linie:iese" sau doar "intra"/"iese"; optional
             "@eticheta" la sfarsit (ex. "poarta:intra@person") — gol = orice trecere
  intrare_zona: numele zonei, optional "@eticheta"; gol = orice zona
  intrus / parcare_inceput: eticheta (person, car...) sau gol
  obiect_nou / obiect_disparut: fara valoare (evenimentele de scena n-au eticheta de clasa)
  zgomot / camera_offline / camera_online: fara valoare

Actiunile ruleaza intr-un thread separat — detectia nu asteapta niciodata dupa o sirena.
Evenimentul "regula" (emis cand o regula se declanseaza) nu poate declansa alte reguli,
deci buclele sunt imposibile prin constructie.
"""
import json
import os
import sqlite3
import threading
import time
import uuid

import jurnal
import settings  # noqa: F401  (folosit indirect; pastrat pentru consecventa modulelor)
import store
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_FILE = os.path.join(ROOT, "data", "rules.json")
LAST_FIRE_FILE = os.path.join(ROOT, "data", "rule_last_fire.json")

# Jurnalul de evenimente sta in SQLite (jurnal.py): retentia e pe ZILE
# (`events_keep_days` din Setari, implicit un an), nu pe octeti ca in JSONL-ul vechi.
# tipurile care pot declansa reguli (evenimentul "regula" lipseste intentionat)
TRIGGER_TYPES = {
    "detectie": "Detectie AI (om, caine, masina...)",
    "persoana": "Persoana recunoscuta (Face ID)",
    "persoana_necunoscuta": "Fata gasita, dar necunoscuta",
    "fata_falsa": "Fata falsa (poza/ecran aratat camerei)",
    "descriere": "Semnalmentele contin un cuvant (ex. rucsac)",
    "obiect": "Obiect urmarit (numarare): eticheta, ex. car",
    "numar": "Numar de inmatriculare citit",
    "alarma_camera": "Alarma din camera (miscare, intruziune...)",
    "trecere": "Linie de numarare trecuta (linia, sensul intra/iese)",
    "intrare_zona": "Obiect intrat intr-o zona desenata (numele zonei)",
    "intrus": "Intrus confirmat de AI la perimetru (om, vehicul)",
    "obiect_nou": "Obiect nou aparut si ramas in cadru",
    "obiect_disparut": "Obiect care era in cadru si a disparut",
    "parcare_inceput": "Vehicul parcat (sta pe loc 2+ minute)",
    "zgomot": "Alarma de zgomot",
    "camera_offline": "Camera a cazut",
    "camera_online": "Camera a revenit",
    "mod": "Modul s-a schimbat (acasa / plecat / noapte)",
}
ACTION_TYPES = {
    "sirena": "Sirena camerei",
    "strobo": "Stroboscop",
    "anunt": "Anunt vocal (TTS) in difuzor",
    "releu": "Releu (iesirea de alarma)",
    "notificare": "Notificare Telegram/email",
    "webhook": "Eveniment 'regula' catre Node-RED",
    "mqtt": "Mesaj MQTT pe un topic (lumini, prize, Home Assistant)",
    "mod": "Trece sistemul in alt mod (acasa / plecat / noapte)",
}
MODURI = ("acasa", "plecat", "noapte")
SABOTAJ = ("shelteralarm", "tamperdetection", "scenechangedetection", "defocusdetection")
ZILE = ["luni", "marti", "miercuri", "joi", "vineri", "sambata", "duminica"]

_lock = threading.Lock()
# rule_id -> ultimul moment declansat. Persistat pe disc: la restart, un cooldown de
# 2 minute nu se pierde, deci o regula nu mai da spam de sirene/notificari daca
# evenimentele vin des imediat dupa repornire.
_last_fire = {k: float(v) for k, v in (store.load_json(LAST_FIRE_FILE, {}) or {}).items()}


# ---- persistenta regulilor ----
_cache = {"mtime": None, "data": []}


def load():
    """Regulile, cu cache pe mtime — se evalueaza la FIECARE eveniment (si o noapte
    cu vant inseamna mii de alarme de miscare), deci nu recitim discul degeaba."""
    try:
        mtime = os.path.getmtime(RULES_FILE)
    except FileNotFoundError:
        return []
    if _cache["mtime"] != mtime:
        try:
            with open(RULES_FILE) as f:
                data = json.load(f)
            _cache["data"] = data if isinstance(data, list) else []
            _cache["mtime"] = mtime
        except (ValueError, OSError) as e:
            # fisier corupt = TOATE automatizarile moarte; fara mesaj n-ar sti nimeni
            logare.error(f"[rules] nu pot citi regulile: {str(e)[:80]} — "
                  f"raman cele din cache ({len(_cache['data'])})")
    return _cache["data"]


def save(rules):
    with _lock:
        store.atomic_write_json(RULES_FILE, rules, mode=0o600, indent=2)
        # invalidare explicita: doua salvari in aceeasi clipa pot avea acelasi mtime
        _cache["mtime"] = None


def validate(rule):
    """Curata si valideaza o regula venita din UI. Intoarce regula normalizata."""
    tip = (rule.get("declansator") or {}).get("tip")
    if tip not in TRIGGER_TYPES:
        raise ValueError(f"declanșator necunoscut: {tip}")
    nume = (rule.get("nume") or "").strip()[:60]
    if not nume:
        raise ValueError("regula are nevoie de un nume")
    actiuni = []
    for a in rule.get("actiuni") or []:
        if a.get("tip") not in ACTION_TYPES:
            raise ValueError(f"acțiune necunoscută: {a.get('tip')}")
        if a["tip"] == "mod" and a.get("mod") not in MODURI:
            raise ValueError("actiunea 'mod' are nevoie de un mod: acasa / plecat / noapte")
        actiuni.append(a)
    moduri = sorted({str(m) for m in (rule.get("moduri") or []) if m in MODURI})
    if not actiuni:
        raise ValueError("regula are nevoie de cel puțin o acțiune")
    interval = rule.get("interval")
    if interval:
        try:
            for hhmm in interval:
                h, m = hhmm.split(":")
                assert 0 <= int(h) <= 23 and 0 <= int(m) <= 59
        except (ValueError, AssertionError, AttributeError):
            raise ValueError("interval orar invalid (format HH:MM)")
        interval = list(interval[:2])
    zile = sorted({int(z) for z in (rule.get("zile") or []) if 0 <= int(z) <= 6})
    return {"id": rule.get("id") or uuid.uuid4().hex[:12],
            "nume": nume,
            "activ": bool(rule.get("activ", True)),
            "declansator": {"tip": tip,
                            "valoare": str((rule["declansator"].get("valoare") or "")).strip()},
            "camere": sorted({int(c) for c in (rule.get("camere") or [])}),
            "grupuri": sorted({str(g).strip()[:30]
                               for g in (rule.get("grupuri") or []) if str(g).strip()}),
            "interval": interval or None,
            "zile": zile if len(zile) < 7 else [],   # toate 7 = fara restrictie
            "moduri": moduri if len(moduri) < 3 else [],   # toate 3 = fara restrictie
            "cooldown": max(5, min(3600, int(rule.get("cooldown") or 60))),
            "actiuni": actiuni}


def upsert(rule):
    rule = validate(rule)
    rules = load()
    rules = [r for r in rules if r.get("id") != rule["id"]]
    rules.append(rule)
    save(rules)
    return rule


def delete(rule_id):
    rules = load()
    rest = [r for r in rules if r.get("id") != rule_id]
    if len(rest) == len(rules):
        raise ValueError("regula nu există")
    save(rest)


# ---- jurnalul permanent de evenimente ----
# ---- severitatea: cat de urgent e un eveniment pentru un operator ----
# Nu toate evenimentele sunt egale: o fata necunoscuta noaptea la poarta cere reactie
# imediata, o gaina nu. Severitatea (info / atentie / critic) e calculata la jurnalizare
# si scoate in fata, in panoul de incidente, ce conteaza. Pura si testabila.
SEV_RANK = {"info": 0, "atentie": 1, "critic": 2}
VEHICULE = {"car", "truck", "bus", "motorcycle"}


def severity(tip, date, is_night):
    """info / atentie / critic, dupa tip + context + zi/noapte. Fara efecte."""
    d = date or {}
    if tip == "fata_falsa":
        return "critic"                                  # tentativa de pacaleala
    if tip == "persoana_necunoscuta":
        return "critic" if is_night else "atentie"
    if tip == "persoana":
        return "info"                                    # persoana cunoscuta
    if tip == "numar":
        if d.get("nume"):
            return "info"                                # masina cunoscuta
        return "atentie" if is_night else "info"
    if tip in ("detectie", "obiect"):
        # TOATE detectiile brute sunt info (si person, din 2026-09-10): prezenta
        # umana e acoperita de EPISOADE (tranzit/zabovire, zi/noapte), iar
        # perimetrul de "intrus" — triajul nu mai vede acelasi om de doua ori
        return "info"
    if tip == "intrus":
        # alarma de perimetru CONFIRMATA de AI (om/vehicul real pe cadrul HD)
        if d.get("eticheta") == "person":
            return "critic" if is_night else "atentie"   # om la perimetru, noaptea = grav
        return "atentie"                                 # vehicul la perimetru
    if tip == "alarma_camera":
        # sabotajul (obiectiv acoperit, sabotaj, scena schimbata) e de triat ziua; noaptea
        # IR-ul si insectele dau „obiectiv acoperit" pe banda (cam3/cam4: ~30 pe noapte,
        # 2026-10-24), deci ramane info — regulile il pot urmari oricum, cu cooldown
        if d.get("tip") in SABOTAJ:
            return "info" if is_night else "atentie"
        # alarmele BRUTE ale camerei raman info: VMD e zgomot, iar linie/intrare sunt
        # confirmate separat de AI (evenimentul 'intrus'). Asa incidentele raman curate,
        # fara cele ~90% fals-pozitive de la pomi/vant.
        return "info"
    if tip in ("zgomot", "camera_offline"):
        return "atentie"
    if tip in ("parcare_inceput", "parcare_sfarsit"):
        # tranzitiile parcarii sunt exact incidentele cerute: cine a venit/plecat
        # si cat a stat — starea insasi (masina pe loc) nu mai alerteaza deloc
        return "atentie"
    if tip == "episod":
        # rezumatul unei prezente umane: cineva CUNOSCUT = doar consemnare;
        # necunoscut = de triat, iar noaptea e exact "omul pe alee" — grav.
        # EXCEPTIE: trecerea scurta prin cadru e pietonul de pe strada, nu un
        # intrus — primul episod real (2026-09-09 22:06, cam14, 1s) iesea
        # "critic" si ar fi umplut triajul nocturn cu trecatori.
        if d.get("nume"):
            return "info"
        # <30s, aliniat cu pragul de zabovire: o traversare normala de strada ia
        # 10-20s (al doilea episod real, 14s, iesea tot critic); peste 30s
        # comportamentul devine oricum "a zabovit"/"a stat" si ramane critic
        if (d.get("durata_sec") or 0) < 30 and d.get("comportament") == "a trecut prin cadru":
            return "atentie" if is_night else "info"
        return "critic" if is_night else "atentie"
    if tip in ("obiect_nou", "obiect_disparut"):
        # schimbare statica pe o camera vegheata (obiect lasat/luat). Ramane INFO: rata de
        # fals-pozitiv (lumina/vegetatie) e prea mare ca sa inunde panoul de triaj — se
        # vede in jurnal si cautare, nu ca incident de tratat.
        return "info"
    return "info"


def event_severity(e):
    """Severitatea unui eveniment din jurnal, RECALCULATA din ora lui — ca schimbarile
    de logica (ex. VMD -> info) sa se aplice si retroactiv, fara a rescrie jurnalul."""
    date = {}
    if e.get("subtip"):
        date["tip"] = e["subtip"]
    for k in ("eticheta", "nume", "numar", "durata_sec", "comportament"):
        if e.get(k) is not None:
            date[k] = e[k]
    hhmm = (e.get("t") or "")[11:16] or None
    return severity(e.get("tip"), date, _is_night(hhmm))


def _is_night(now_hhmm=None):
    import settings
    interval = [settings.get("night_start") or "22:00",
                settings.get("night_end") or "06:00"]
    return _in_interval(interval, now_hhmm or time.strftime("%H:%M"))


def _journal(tip, cam_id, date):
    now = time.time()
    entry = {"id": uuid.uuid4().hex[:12],
             "t": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)), "tip": tip,
             "sev": severity(tip, date, _is_night())}
    if cam_id is not None:
        entry["cam"] = cam_id
    # doar campurile plate, serializabile si scurte — jurnalul e istorie, nu depozit.
    # "tip" din date (subtipul alarmei de camera: VMD, linedetection...) nu are voie
    # sa suprascrie tipul evenimentului — devine "subtip".
    for k, v in date.items():
        if k in ("id", "t", "tip", "cam", "sev"):
            k = "subtip" if k == "tip" else "ev_" + k
        if isinstance(v, (str, int, float, bool)) and len(str(v)) <= 120:
            entry[k] = v
        elif isinstance(v, list) and len(str(v)) <= 120:
            entry[k] = v
        elif k == "traseu" and isinstance(v, list) and len(str(v)) <= 2000:
            # singura lista "mare" permisa: mini-harta episodului (E3) — fara ea
            # cardul n-are traseu; episoadele sunt putine, jurnalul nu se umfla
            entry[k] = v
    try:
        jurnal.insert(entry, ts=now)
    except sqlite3.Error as e:
        # jurnalul nu are voie sa doboare lantul de evenimente (reguli, sirene)
        logare.info(f"[rules] jurnal: {str(e)[:80]}")
    if entry["sev"] != "info":
        # notificare push pe telefoanele abonate (push.py decide nivelul si camerele);
        # tot ce e >= atentie e un incident — exact ce arata si panoul „De tratat"
        try:
            import push
            push.eveniment(entry)
        except Exception as e:
            logare.info(f"[rules] push: {str(e)[:80]}")


def journal(limit=200, tip=None, cam=None, day=None, min_sev=None):
    """Cele mai noi evenimente, filtrabile dupa tip / camera / zi / severitate
    minima. Cele mai noi primele; citire indexata din SQLite (jurnal.py)."""
    return jurnal.query(limit=limit, tip=tip, cam=cam, day=day, min_sev=min_sev)


# ---- episoade: evenimentele grupate, cu inceput/sfarsit/durata ----
# Un caine care se plimba 4 minute prin curte produce zeci de evenimente "detectie";
# ca lista sunt zgomot, ca episod sunt un rand: inceput, sfarsit, durata, de cate ori.
EPISODE_GAP = 300      # implicit; se foloseste incident_gap_sec din Setari daca exista
EPISODE_MAX = 1800     # un episod se taie la 30 min, ca sa ramana navigabil


def _epoch(t):
    return time.mktime(time.strptime(t, "%Y-%m-%d %H:%M:%S"))


def episodes(limit=100, tip=None, cam=None, day=None, gap=None, min_sev=None):
    """Jurnalul grupat in episoade. Cheia de grupare: (camera, tip, detaliul principal)
    — "caine pe cam5" si "om pe cam5" raman episoade separate chiar daca se suprapun."""
    if gap is None:
        try:
            gap = int(settings.get("incident_gap_sec") or EPISODE_GAP)
        except Exception:
            gap = EPISODE_GAP
    evs = journal(limit=5000, tip=tip, cam=cam, day=day, min_sev=min_sev)
    evs.reverse()                       # cronologic, ca sa se lege corect
    deschise, out = {}, []
    for e in evs:
        try:
            ts = e["ts"] if e.get("ts") is not None else _epoch(e["t"])
        except (KeyError, ValueError):
            continue
        detaliu = (e.get("nume") or e.get("numar") or e.get("eticheta")
                   or e.get("subtip") or e.get("regula") or "")
        k = (e.get("cam"), e.get("tip"), detaliu)
        sev = e.get("sev", "info")
        ep = deschise.get(k)
        if ep and ts - ep["_last"] <= gap and ts - ep["_start"] < EPISODE_MAX:
            ep["_last"] = ts
            ep["sfarsit"] = e["t"]
            ep["evenimente"] += 1
            if SEV_RANK.get(sev, 0) > SEV_RANK.get(ep["sev"], 0):
                ep["sev"] = sev             # episodul poarta severitatea lui maxima
        else:
            ep = {"cam": e.get("cam"), "tip": e.get("tip"), "detaliu": detaliu,
                  "inceput": e["t"], "sfarsit": e["t"], "evenimente": 1,
                  "sev": sev, "_start": ts, "_last": ts}
            deschise[k] = ep
            out.append(ep)
    for ep in out:
        ep["durata"] = int(ep["_last"] - ep["_start"])
        ep.pop("_start")
        ep.pop("_last")
    out.reverse()                       # cele mai noi primele
    return out[:limit]


# ---- evaluarea ----
def _in_interval(interval, now_hhmm):
    """["23:00","06:00"] contine 23:30 si 05:00, dar nu 12:00 (trece peste miezul noptii)."""
    if not interval:
        return True
    start, end = interval
    if start <= end:
        return start <= now_hhmm < end
    return now_hhmm >= start or now_hhmm < end


def _value_match(tip, valoare, date):
    """Se potriveste valoarea regulii cu datele evenimentului?"""
    if not valoare:
        return True
    if tip in ("detectie", "obiect"):
        if tip == "obiect" and valoare == "biciclist":
            return bool(date.get("biciclist"))       # om urmarit cu viteza de bicicleta
        return date.get("eticheta") == valoare
    if tip == "persoana":
        return date.get("nume") == valoare
    if tip == "numar":
        if valoare == "cunoscut":
            return bool(date.get("nume"))
        if valoare == "necunoscut":
            return not date.get("nume")
        return date.get("numar") == valoare.upper()
    if tip == "alarma_camera":
        # subtipul raportat de camera: VMD, linedetection, fielddetection...
        return date.get("tip") == valoare
    if tip == "descriere":
        # "rucsac" declanseaza daca semnalmentele contin cuvantul
        return valoare.lower() in str(date.get("text") or "").lower()
    if tip == "mod":
        return date.get("mod") == valoare
    if tip in ("trecere", "intrare_zona"):
        # "poarta:intra@person" -> locul "poarta", sensul "intra", eticheta "person"
        loc, _, et = valoare.partition("@")
        if et and date.get("eticheta") != et:
            return False
        if tip == "intrare_zona":
            return not loc or date.get("zona") == loc
        nume, _, sens = loc.partition(":")
        if nume in ("intra", "iese") and not sens:   # doar sensul, orice linie
            nume, sens = "", nume
        if nume and date.get("linie") != nume:
            return False
        return not sens or date.get("sens") == sens
    if tip in ("obiect_nou", "obiect_disparut"):
        return True                  # evenimentele de scena n-au eticheta de clasa (doar fel/unde)
    if tip in ("intrus", "parcare_inceput"):
        return date.get("eticheta") == valoare
    return True


def matches(rule, tip, cam_id, date, now_hhmm=None, weekday=None, cam_group=None, mod_curent=None):
    """Toata logica de potrivire, pura si testabila — fara efecte.

    Camerele si grupurile (zonele) se combina cu SAU: camera e potrivita daca e in
    lista de camere SAU grupul ei e in lista de grupuri; ambele goale = oriunde.
    `moduri` (acasa/plecat/noapte) restrange regula la modurile alese; gol = oricand."""
    if not rule.get("activ", True):
        return False
    if rule["declansator"]["tip"] != tip:
        return False
    if rule.get("moduri"):
        if mod_curent is None:
            import mod
            mod_curent = mod.curent()
        if mod_curent not in rule["moduri"]:
            return False
    camere, grupuri = rule.get("camere") or [], rule.get("grupuri") or []
    if camere or grupuri:
        in_camere = cam_id in camere
        in_grup = cam_group is not None and cam_group in grupuri
        if not (in_camere or in_grup):
            return False
    if rule.get("zile"):
        wd = weekday if weekday is not None else time.localtime().tm_wday  # 0 = luni
        if wd not in rule["zile"]:
            return False
    if not _in_interval(rule.get("interval"),
                        now_hhmm or time.strftime("%H:%M")):
        return False
    return _value_match(tip, rule["declansator"].get("valoare", ""), date)


def on_event(tip, cam_id, date):
    """Punctul de intrare, chemat din hooks.emit pentru ORICE eveniment.
    Jurnalizeaza (cu exceptia VMD); evalueaza regulile doar pentru tipurile declansatoare."""
    # VMD (motion brut al camerei) e pur zgomot ~15k/zi si oricum ramane 'info'. NU-l
    # jurnalizam: altfel impinge evenimentele reale (intrus, descrieri) afara din
    # events.jsonl in ~40h. Ramane declansator pt. reguli + confirmarea de perimetru
    # (facuta in camctl inainte de emit) + poza in tabul Alarme (snaps, separat de jurnal).
    is_vmd = tip == "alarma_camera" and (date or {}).get("tip") == "VMD"
    if tip != "regula" and not is_vmd:   # "regula" se jurnalizeaza la declansare, cu rezultate
        _journal(tip, cam_id, date)
    if tip not in TRIGGER_TYPES:
        return
    now = time.time()
    grup = ((_cam_entry(cam_id) or {}).get("group") or "Altele") if cam_id else None
    for rule in load():
        try:
            if not matches(rule, tip, cam_id, date, cam_group=grup):
                continue
            if now - _last_fire.get(rule["id"], 0) < rule.get("cooldown", 60):
                continue
            _last_fire[rule["id"]] = now
            try:                       # persistam cooldown-ul (rar: doar cand chiar declanseaza)
                store.atomic_write_json(LAST_FIRE_FILE, _last_fire, mode=0o600)
            except OSError as e:
                logare.info(f"[rules] persist last_fire: {str(e)[:60]}")
            threading.Thread(target=_run_actions, args=(rule, tip, cam_id, date),
                             daemon=True).start()
        except Exception as e:
            logare.info(f"[rules] regula '{rule.get('nume')}': {str(e)[:80]}")


def _cam_entry(cam_id):
    try:
        with open(os.path.join(ROOT, "data", "cameras.json")) as f:
            return next((c for c in json.load(f) if c["id"] == cam_id), None)
    except (FileNotFoundError, ValueError):
        return None


def _run_actions(rule, tip, cam_id, date):
    """Executa actiunile unei reguli declansate. Fiecare esec se jurnalizeaza si nu
    opreste actiunile urmatoare — sirena tot suna daca notificarea a picat."""
    import audit
    import drivers as camctl        # actiunile trec prin driver (Hikvision/ONVIF)
    import notify
    cam = _cam_entry(cam_id) if cam_id is not None else None
    feat = (cam or {}).get("features") or {}
    context = date.get("nume") or date.get("numar") or date.get("eticheta") or tip
    rezultate = []
    for a in rule["actiuni"]:
        at = a["tip"]
        try:
            if at == "sirena":
                if not feat.get("siren"):
                    raise RuntimeError("camera nu are sirena")
                snd = str(a.get("sunet") or 1)
                camctl.trigger_siren(cam, audio_id=int(snd.lstrip("c")),
                                     volume=int(a.get("volum") or 90),
                                     times=int(a.get("repetari") or 1),
                                     custom=snd.startswith("c"))
            elif at == "strobo":
                if not feat.get("strobe"):
                    raise RuntimeError("camera nu are stroboscop")
                camctl.set_strobe(cam, int(a.get("secunde") or 30))
            elif at == "anunt":
                if not feat.get("talk"):
                    raise RuntimeError("camera nu are difuzor")
                text = (a.get("text") or "").strip() or f"Atentie: {context}"
                camctl.speak(cam, text, repeat=int(a.get("repetari") or 1))
            elif at == "releu":
                if not feat.get("io_out"):
                    raise RuntimeError("camera nu are releu")
                camctl.set_output(cam, int(a.get("port") or 1), True)
                sec = int(a.get("secunde") or 0)
                if sec:
                    time.sleep(min(sec, 300))
                    camctl.set_output(cam, int(a.get("port") or 1), False)
            elif at == "notificare":
                nume_cam = (cam or {}).get("name") or (f"cam {cam_id}" if cam_id else "sistem")
                text = (a.get("text") or "").strip() or f"Regula „{rule['nume']}”: {context}"
                notify.send(f"⚡ {nume_cam}: {text}" + notify.link(cam_id),
                            key=("regula", rule["id"]))
            elif at == "webhook":
                import hooks
                hooks.emit("regula", cam_id, regula=rule["nume"], declansator=tip,
                           context=context)
            elif at == "mod":
                import mod
                mod.set(a.get("mod"), cine=f"regula:{rule['nume']}", sursa="regula")
            elif at == "mqtt":
                import mqtt
                topic = (a.get("topic") or "").strip()
                if not topic:
                    raise RuntimeError("acțiunea mqtt are nevoie de un topic")
                payload = (a.get("payload") or "").strip() or json.dumps(
                    {"regula": rule["nume"], "declansator": tip, "context": context,
                     "camera": cam_id}, ensure_ascii=False)
                if not mqtt.publish(topic, payload):
                    raise RuntimeError("publicarea a eșuat (broker oprit sau MQTT dezactivat)")
            rezultate.append(f"{at} ok")
        except Exception as e:
            rezultate.append(f"{at}: {str(e)[:60]}")
    _journal("regula", cam_id, {"regula": rule["nume"], "declansator": tip,
                                "context": context, "actiuni": rezultate})
    audit.log("regula declansata", rule["nume"],
              {"declansator": tip, "context": context, "actiuni": rezultate})
    logare.info(f"[rules] '{rule['nume']}' ({tip}/{context}): {', '.join(rezultate)}")
