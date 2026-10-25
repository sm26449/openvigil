#!/usr/bin/env python3
"""Jurnal de modificari: cine, cand, ce a schimbat in configuratie.

Fara el, peste o luna te intrebi de ce o camera are alt bitrate si nu ai cum sa afli.
Scriem linie cu linie (JSONL), pastram ultimele ~20000 de intrari.

Intr-o cerere HTTP, `who` se completeaza singur cu UTILIZATORUL logat (si `ip` cu adresa
lui), deci apelantii nu mai trebuie sa trimita nimic. Tot aici e si plasa de siguranta:
`cerere_neauditata()` (chemata din after_request) scrie orice POST/PUT/PATCH/DELETE reusit
pe care nicio ruta nu l-a jurnalizat explicit — o actiune noua nu poate ramane neauditata
din uitare. `log()` marcheaza cererea curenta (flask.g) ca auditata, ca sa nu dubleze.
"""
import json
import os
import threading
import time
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "data", "changes.jsonl")
MAX_LINES = 20000
# campuri care nu au voie in jurnal (parole, jetoane, coduri, chei de push)
SECRETE = ("pass", "pass2", "parola", "password", "password2", "web_password",
           "web_password_new", "telegram_token", "email_pass", "api_token", "webhook_secret",
           "mqtt_pass", "secret", "cod", "totp", "token", "keys", "abonament", "hash")
# ce nu auditam generic: au jurnalul lor (login, API-ul cu jeton, canalul nvr-ai) sau nu
# sunt modificari (operatorul apasa PTZ de zeci de ori pe minut; audit.log explicit ramane)
NEAUDITAT = ("/login", "/setup", "/logout", "/api/internal/", "/api/v1/")
_lock = threading.Lock()


def _context():
    """(utilizator, ip) din cererea Flask curenta, daca exista una."""
    try:
        from flask import g, has_request_context, request, session
        if not has_request_context():
            return "", ""
        g._auditat = True
        return session.get("user") or "", request.remote_addr or ""
    except Exception:
        return "", ""


def log(action, target="", detail=None, who=""):
    """Adauga o intrare. action = ce s-a facut, target = pe ce, detail = valorile.
    who lipsa = utilizatorul sesiunii curente."""
    user, ip = _context()
    rec = {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "action": action,
           "target": str(target), "who": who or user or "-"}
    if ip:
        rec["ip"] = ip
    if detail:
        rec["detail"] = {k: v for k, v in detail.items() if k not in SECRETE}
    try:
        with _lock:
            os.makedirs(os.path.dirname(FILE), exist_ok=True)
            with open(FILE, "a") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        # jurnalul de modificari nu se poate scrie (disc plin?) — macar in stdout
        logare.error(f"[audit] scriere esuata ({e}): {rec['action']} {rec['target']}")


def _rezumat(body):
    """Corpul unei cereri, scurtat si fara secrete, cat sa se vada CE s-a schimbat."""
    if not isinstance(body, dict):
        return None
    out = {}
    for k, v in list(body.items())[:30]:
        if k in SECRETE:
            continue
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        s = str(v)
        out[k] = s if len(s) <= 80 else s[:77] + "…"
    return out or None


def cerere_neauditata(response):
    """Plasa de siguranta (after_request): orice modificare reusita pe care ruta n-a
    auditat-o explicit se scrie ca „cerere <METODA> <cale>", cu utilizatorul si corpul."""
    try:
        from flask import g, request
        if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
            return
        if response.status_code >= 400 or getattr(g, "_auditat", False):
            return
        p = request.path
        if any(p == x or p.startswith(x) for x in NEAUDITAT):
            return
        body = request.get_json(silent=True) if request.is_json else None
        if body is None and request.form:
            body = request.form.to_dict()
        if body is None and request.files:
            body = {"fisiere": ", ".join(f.filename or "?" for f in request.files.values())}
        log("cerere", f"{request.method} {p}", _rezumat(body))
    except Exception as e:
        logare.info(f"[audit] plasa de siguranta: {str(e)[:80]}")


def tail(n=200, q=""):
    """Ultimele intrari, cele mai noi primele."""
    try:
        with open(FILE) as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    if len(lines) > MAX_LINES:      # taiem periodic, pastram coada
        try:
            import store
            with _lock:
                store.atomic_write_text(FILE, "".join(lines[-MAX_LINES:]), mode=0o644)
        except OSError as e:
            logare.error(f"[audit] taierea jurnalului esuata: {e}")
        lines = lines[-MAX_LINES:]
    out = []
    for ln in reversed(lines):
        try:
            r = json.loads(ln)
        except ValueError:
            continue
        if q and q.lower() not in json.dumps(r, ensure_ascii=False).lower():
            continue
        out.append(r)
        if len(out) >= n:
            break
    return out
