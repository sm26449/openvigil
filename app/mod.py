#!/usr/bin/env python3
"""Modul sistemului: acasa / plecat / noapte — comutatorul de „armare" (Home Mode).

Regulile au program orar, dar nu stiu daca esti acasa: o sirena la om pe alee e buna cand
ai plecat si enervanta cand tunzi iarba. Modul e o stare globala, mica si explicita:
  - citita de motorul de reguli (`moduri` pe regula: in ce moduri e valabila);
  - comutata din interfata (antet), Telegram (/acasa, /plecat, /noapte), MQTT
    (`<baza>/mod/set`), API-ul cu jeton (/api/v1/mod) sau o actiune de regula ("mod");
  - publicata pe MQTT (`<baza>/mod`, retained) si jurnalizata ca eveniment `mod` (deci
    si declansator de reguli: „cand trec pe plecat, spune in difuzor «sistem armat»").
Optional, trece singur acasa -> noapte la night_start si inapoi la night_end
(`mod_auto_noapte` din Setari); „plecat" nu se atinge automat niciodata.
Starea sta in data/mod.json (nu in settings: e stare, nu configurare).
"""
import os
import threading
import time

import store
import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "data", "mod.json")
PROGRAM_FILE = os.path.join(ROOT, "data", "mod_program.json")   # programul saptamanal (timeline)
_ZILE = 7
MODURI = ("acasa", "plecat", "noapte")
MOD_RO = {"acasa": "Acasa", "plecat": "Plecat", "noapte": "Noapte"}
MOD_ICO = {"acasa": "🏠", "plecat": "🔒", "noapte": "🌙"}
_lock = threading.Lock()
_tick_timer = None


def get():
    """{mod, de_cand, cine, sursa} — implicit acasa."""
    d = store.load_json(FILE, None) or {}
    if d.get("mod") not in MODURI:
        d = {"mod": "acasa", "de_cand": None, "cine": None, "sursa": None}
    return d


def curent():
    return get()["mod"]


def set(mod, cine=None, sursa="ui", anunta=True):     # noqa: A001  (numele e API-ul modulului)
    """Schimba modul. Intoarce starea noua; daca modul e deja cel cerut, nu face nimic
    (nici eveniment, nici MQTT) — comutatoarele din 3 surse nu trebuie sa se bata."""
    if mod not in MODURI:
        raise ValueError(t("mod necunoscut: {mod} (acasa / plecat / noapte)", mod=mod))
    with _lock:
        vechi = get()
        if vechi["mod"] == mod:
            return vechi
        nou = {"mod": mod, "de_cand": time.time(), "cine": cine, "sursa": sursa,
               "anterior": vechi["mod"]}
        store.atomic_write_json(FILE, nou, mode=0o600)
    if anunta:
        _anunta(nou)
    return nou


def _anunta(stare):
    """Efectele secundare ale schimbarii: MQTT, audit, eveniment in jurnal (+ reguli)."""
    mod = stare["mod"]
    try:
        import mqtt
        mqtt.publish(f"{mqtt._base()}/mod", mod, retain=True)
    except Exception as e:
        logare.info(f"[mod] mqtt: {str(e)[:60]}")
    try:
        import audit
        audit.log("mod schimbat", MOD_RO[mod], {"sursa": stare.get("sursa"),
                                               "anterior": stare.get("anterior")},
                  who=stare.get("cine") or "")
    except Exception as e:
        logare.info(f"[mod] audit: {str(e)[:60]}")
    try:
        import hooks
        hooks.emit("mod", None, mod=mod, anterior=stare.get("anterior"),
                   sursa=stare.get("sursa"), cine=stare.get("cine"))
    except Exception as e:
        logare.info(f"[mod] eveniment: {str(e)[:60]}")


def publica():
    """Starea curenta pe MQTT (retained) — la conectare, ca Home Assistant sa o vada."""
    try:
        import mqtt
        mqtt.publish(f"{mqtt._base()}/mod", curent(), retain=True)
    except Exception:
        pass


# ---- programul saptamanal (timeline de moduri) ----
# Un „program" e o foaie saptamanala de TRANZITII: pe fiecare zi (0=luni..6=duminica), o lista
# de {h:"HH:MM", mod:...} sortata dupa ora. De la ora unei tranzitii pana la urmatoarea, modul
# programat e cel al tranzitiei. Comutam DOAR in momentul tranzitiei (edge-triggered, cu
# recuperare dupa oprire): asa o schimbare manuala intre doua tranzitii ramane pana la urmatoarea.
# `last_aplicat` = epoch-ul ultimei tranzitii pe care am aplicat-o (idempotenta + anti-reassert).
def _norm_hhmm(x):
    """'H:M' / 'HH:MM' -> 'HH:MM' valid, sau None."""
    try:
        h, m = str(x).strip().split(":")
        h, m = int(h), int(m)
        if 0 <= h < 24 and 0 <= m < 60:
            return f"{h:02d}:{m:02d}"
    except (ValueError, AttributeError):
        pass
    return None


def _zile_normalizate(zile_in, strict=False):
    """{0..6 -> [{h,mod}]} curatat: ore valide, moduri valide, o tranzitie/minut, sortat.
    strict=True ridica ValueError la o ora/mod invalid (la salvare); altfel le sare (la citire)."""
    zile = {}
    for z in range(_ZILE):
        seg = (zile_in or {}).get(str(z)) or (zile_in or {}).get(z) or []
        if not isinstance(seg, list):
            if strict:
                raise ValueError(t("program invalid"))
            seg = []
        buni, vazute = [], []     # NB: in modulul asta `set` e functia de mod, nu built-in-ul
        for s in seg:
            h = _norm_hhmm((s or {}).get("h"))
            m = (s or {}).get("mod")
            if not h:
                if strict:
                    raise ValueError(t("oră invalidă în program (HH:MM)"))
                continue
            if m not in MODURI:
                if strict:
                    raise ValueError(t("mod necunoscut: {mod} (acasa / plecat / noapte)", mod=m))
                continue
            if h in vazute:
                continue      # o singura tranzitie pe minut intr-o zi
            vazute.append(h)
            buni.append({"h": h, "mod": m})
        buni.sort(key=lambda s: s["h"])
        zile[str(z)] = buni
    return zile


def program():
    """Programul saptamanal: {activ, zile:{"0".."6":[{h,mod}]}, last_aplicat}. Implicit inactiv."""
    d = store.load_json(PROGRAM_FILE, None) or {}
    return {"activ": bool(d.get("activ")),
            "zile": _zile_normalizate(d.get("zile")),
            "last_aplicat": float(d.get("last_aplicat") or 0)}


def set_program(data):
    """Valideaza si salveaza programul (pastreaza `last_aplicat`). Ridica ValueError la date rele."""
    if not isinstance(data, dict):
        raise ValueError(t("program invalid"))
    zile = _zile_normalizate(data.get("zile"), strict=True)
    with _lock:
        vechi = program()
        nou = {"activ": bool(data.get("activ")), "zile": zile,
               "last_aplicat": vechi.get("last_aplicat", 0)}
        store.atomic_write_json(PROGRAM_FILE, nou, mode=0o600)
    return nou


def _tranzitie_curenta(prg, now):
    """Cea mai recenta tranzitie cu epoch <= now (azi cu h<=acum, apoi zilele anterioare pana
    la 7). Intoarce (epoch, mod) sau None daca programul n-are nicio tranzitie."""
    zile = prg["zile"]
    now_hhmm = time.strftime("%H:%M", time.localtime(now))
    for d in range(0, _ZILE + 1):
        ziua = time.localtime(now - d * 86400)
        seg = zile.get(str(ziua.tm_wday)) or []       # tm_wday: 0=luni
        cand = [s for s in seg if s["h"] <= now_hhmm] if d == 0 else seg
        if not cand:
            continue
        s = cand[-1]        # ultima (cea mai tarzie) — listele sunt sortate
        hh, mm = (int(x) for x in s["h"].split(":"))
        epoch = time.mktime((ziua.tm_year, ziua.tm_mon, ziua.tm_mday, hh, mm, 0, 0, 0, -1))
        return epoch, s["mod"]
    return None


def _tick_program(prg, now):
    """Aplica tranzitia programata daca e mai noua decat ultima aplicata. Intoarce modul nou sau None."""
    tr = _tranzitie_curenta(prg, now)
    if not tr:
        return None
    epoch, mod = tr
    if epoch <= prg.get("last_aplicat", 0):
        return None        # deja aplicata (sau mai veche) — nu calcam peste o schimbare manuala
    with _lock:
        cur = program()                  # reciteste, ca sa nu pierdem o editare concurenta
        cur["last_aplicat"] = epoch
        store.atomic_write_json(PROGRAM_FILE, cur, mode=0o600)
    if curent() != mod:
        set(mod, cine="program", sursa="program")
        return mod
    return None


# ---- noaptea automata (fereastra unica, folosita cand programul saptamanal e inactiv) ----
def _in_noapte(hhmm, start, end):
    if start <= end:
        return start <= hhmm < end
    return hhmm >= start or hhmm < end


def tick(now_hhmm=None, now=None):
    """Chemata la fiecare minut (testabila cu ora/epoch date). Daca programul saptamanal e
    activ, aplica tranzitiile lui; altfel, fereastra unica de noapte (`mod_auto_noapte`):
    acasa -> noapte la inceputul noptii, noapte -> acasa la sfarsit („plecat" nu se atinge).
    Intoarce modul nou sau None daca n-a schimbat nimic."""
    prg = program()
    if prg.get("activ"):
        return _tick_program(prg, time.time() if now is None else now)
    import settings
    if not settings.get("mod_auto_noapte"):
        return None
    hhmm = now_hhmm or time.strftime("%H:%M")
    noapte = _in_noapte(hhmm, settings.get("night_start") or "22:00",
                        settings.get("night_end") or "06:00")
    m = curent()
    if noapte and m == "acasa":
        return set("noapte", cine="program", sursa="program")["mod"]
    if not noapte and m == "noapte":
        return set("acasa", cine="program", sursa="program")["mod"]
    return None


def start():
    """Programatorul de noapte: un fir care verifica la fiecare minut."""
    def bucla():
        while True:
            time.sleep(60)
            try:
                tick()
            except Exception as e:
                logare.info(f"[mod] tick: {str(e)[:60]}")
    threading.Thread(target=bucla, daemon=True, name="mod-tick").start()
    publica()
