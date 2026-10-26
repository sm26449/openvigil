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


# ---- noaptea automata ----
def _in_noapte(hhmm, start, end):
    if start <= end:
        return start <= hhmm < end
    return hhmm >= start or hhmm < end


def tick(now_hhmm=None):
    """Chemata la fiecare minut (si testabila cu ora data): acasa -> noapte la inceputul
    noptii, noapte -> acasa la sfarsit. Doar daca `mod_auto_noapte` e pornit; „plecat"
    ramane cum e. Intoarce modul nou sau None daca n-a schimbat nimic."""
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
