#!/usr/bin/env python3
"""Escaladarea alertelor critice către un serviciu extern de notificări, printr-un
`POST /v1/fire` (contractul: `{"type": ..., "context": {...}}`, antet `X-API-Key`). Serviciul
decide canalele (Telegram, SMS, email…), throttling-ul și retry-ul — noi doar „tragem".

Decuplat și opțional: dacă setarea `alert_service_key` e goală, nu facem nimic. Niciodată nu
ridică și nu blochează (best-effort, timeout scurt, în fir) — o regulă critică (ex. căderea unei
persoane) nu are voie să cadă fiindcă escaladarea nu răspunde; alerta primară a NVR-ului pleacă
oricum pe canalele proprii. Vezi docs/reguli-analiza.md și docs/alerte.md.

`alerta()` trimite prin serviciu și alertele operaționale (cameră/NVR căzut, disc, înregistrare,
copie de siguranță, rezumat…), câte un tip `nvr_*` fiecare: canalele, SMS-ul, rate-limit-ul și
confirmarea le decide serviciul. Botul propriu (notify.send) rămâne rezervă: dacă serviciul nu e
configurat, nu răspunde, dă eroare, nu cunoaște tipul sau toate canalele lui au eșuat, mesajul
pleacă pe Telegram/email ca înainte. O suprimare voită (throttle, ore de liniște, amânare) NU
cade pe rezervă — e decizia serviciului. Vezi docs/alerte.md.
"""
import re
import threading
import time

import requests

import settings
import logare


def activ():
    """Escaladarea e configurată? (URL + cheie prezente)."""
    return bool(settings.get("alert_service_url") and settings.get("alert_service_key"))


def escaladeaza(context, tip=None, sync=False):
    """Trimite o escaladare. `tip` suprascrie `alert_service_type`. Întoarce True dacă a plecat
    (sau a fost pusă în fir), False dacă escaladarea e oprită. sync=True pentru teste/butoane."""
    if not activ():
        return False
    url = settings.get("alert_service_url")
    key = settings.get("alert_service_key")
    payload = {"type": tip or settings.get("alert_service_type") or "nvr_cadere",
               "context": dict(context or {})}

    def work():
        try:
            r = requests.post(url, json=payload, headers={"X-API-Key": key}, timeout=8)
            if r.ok:
                logare.info(f"[escaladare] {payload['type']} trimis")
            else:
                logare.info(f"[escaladare] {payload['type']}: HTTP {r.status_code} {r.text[:80]}")
        except Exception as e:
            logare.info(f"[escaladare] {payload['type']}: {str(e)[:80]}")

    if sync:
        work()
    else:
        threading.Thread(target=work, daemon=True, name="escaladare").start()
    return True


# suprimari voite (decizia serviciului) -> fara rezerva; restul (unknown_type, eroare) -> rezerva
REZERVA_LA = {"unknown_type"}
# starile din contract (docs/alerte.md); cand nicio notificare nu descrie apelul, `status` e
# chiar valoarea episodului (repeat / resolved / not_found)
STARI = {"sent", "partial", "pending", "failed", "suppressed", "opened", "repeat", "resolved", "not_found"}
_EPISOD_OK = re.compile(r"[^A-Za-z0-9._:@/-]")
ultima = {}       # ultimul raspuns, pentru Setari (tip, stare, motiv, t)


def _episod(cheie):
    return _EPISOD_OK.sub("-", f"openvigil:{cheie}")[:128] if cheie is not None else None


def are_nevoie_de_rezerva(r):
    """Pur si testabil: raspunsul serviciului (dict) sau None (n-a raspuns) -> pleaca si pe
    botul propriu? `pending` = livrare in curs (nu e esec); `not_found` la o rezolvare = episodul
    s-a deschis pe rezerva, deci si revenirea merge tot acolo."""
    if not isinstance(r, dict):
        return True
    stare = r.get("status")
    # o stare necunoscuta (serviciu incompatibil, proxy care raspunde 200 cu altceva) nu e
    # o confirmare de livrare: alerta pleaca si pe rezerva (audit F4)
    if stare == "failed" or stare not in STARI:
        return True
    if stare == "suppressed":
        return r.get("suppressed_reason") in REZERVA_LA
    return r.get("episode") == "not_found"


def fire(payload, timeout=8):
    """Un POST /v1/fire. Intoarce raspunsul (dict) sau None la orice esec (retea, HTTP, JSON)."""
    try:
        r = requests.post(settings.get("alert_service_url"), json=payload,
                          headers={"X-API-Key": settings.get("alert_service_key")}, timeout=timeout)
        if not r.ok:
            logare.info(f"[alerte] {payload['type']}: HTTP {r.status_code} {r.text[:80]}")
            return None
        d = r.json()
    except Exception as e:
        logare.info(f"[alerte] {payload['type']}: {str(e)[:80]}")
        return None
    if not isinstance(d, dict):                 # 200 cu o lista / null / un numar: nu e raspunsul contractului
        logare.info(f"[alerte] {payload['type']}: raspuns neasteptat ({type(d).__name__})")
        return None
    ultima.update(tip=payload["type"], stare=d.get("status"), motiv=d.get("suppressed_reason"),
                  episod=d.get("episode"), t=time.time())
    if d.get("status") in ("failed", "suppressed"):
        logare.info(f"[alerte] {payload['type']}: {d.get('status')} {d.get('suppressed_reason') or d.get('episode') or ''}")
    return d


def alerta(tip, text, *, episod=None, rezolvat=False, revenire=None, key=None, jpeg=None,
           rezerva=True, sync=False, test=False, **context):
    """Alerta operationala `tip` (sablon nvr_* din serviciu). `text` = mesajul gata compus
    (limba instalatiei) — merge in context ca {text} si e si mesajul de rezerva.
    episod = cheie stabila (ex. "cam-6"): cat timp e deschis, serviciul nu repeta; rezolvat=True
    il inchide si trimite `revenire` (sablonul dat la deschidere). key = throttle-ul botului
    propriu. rezerva=False: copie doar in serviciu (ex. intrus — poza a plecat deja pe bot).
    Intoarce raspunsul serviciului cu sync=True, altfel None."""
    import notify

    def pe_bot():
        if rezerva:
            notify.send(text, jpeg=jpeg, key=key, sync=sync)

    if not activ():
        return pe_bot()
    ctx = {k: v for k, v in context.items() if v is not None}
    ctx["text"] = text
    ctx.setdefault("link", "")
    payload = {"type": tip, "context": ctx, "source": "openvigil"}
    if test:
        payload["test"] = True
    ep = _episod(episod)
    if ep:
        payload["idempotency_key"] = ep
        if rezolvat:
            payload["status"] = "resolved"
        elif revenire:
            payload["resolved_type"] = revenire

    def work():
        r = fire(payload)
        if are_nevoie_de_rezerva(r):
            pe_bot()
        return r

    if sync:
        return work()

    def safe():
        try:
            work()
        except Exception as e:
            logare.info(f"[alerte] {tip}: {str(e)[:80]}")
    threading.Thread(target=safe, daemon=True, name="alerta").start()
    return None
