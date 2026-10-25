"""Utilizatori si roluri pentru interfata web.

Trei roluri:
  admin    — poate tot
  operator — rolul de paznic: vede Live/Inregistrari/Alarme SI poate actiona
             (sirena, vorbit, stroboscop, releu, PTZ, pozitii) pe camerele lui,
             dar nu poate configura nimic
  viewer   — doar vede; niciun POST/DELETE nu trece de guard-ul din server.py
Operatorii si viewerii pot fi restransi la anumite grupuri si/sau camere; fara
restrictii, vad toate camerele.

Fisierul data/users.json tine lista de conturi cu parolele HASH-uite (scrypt, prin
werkzeug) si se scrie cu chmod 600. Pe cont mai stau: `sess_ver` (versiunea sesiunilor —
cookie-urile cu alta versiune sunt moarte: schimbarea parolei sau „deconecteaza toate
dispozitivele" o incrementeaza) si `totp` (autentificarea in doi pasi: secretul, ultimul
contor acceptat si codurile de recuperare hash-uite). Nu exista cont implicit: la prima pornire serverul
te duce la /setup ca sa creezi contul de administrator (sau migreaza automat vechea
parola unica din Setari intr-un cont "admin").
"""
import json
import os
import threading

from werkzeug.security import check_password_hash, generate_password_hash

import store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "data", "users.json")
ROLES = ("admin", "operator", "viewer")
_lock = threading.Lock()


def load():
    try:
        with open(PATH) as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _write(lst):
    """Scriere atomica, fara lock (apelantul detine _lock daca face RMW)."""
    store.atomic_write_json(PATH, lst, mode=0o600, indent=2)


def save(lst):
    with _lock:
        _write(lst)


def get(name):
    return next((u for u in load() if u["user"] == name), None)


def verify(name, password):
    """Contul, daca parola e corecta — altfel None."""
    u = get(name)
    if u and check_password_hash(u.get("hash", ""), password or ""):
        return u
    return None


def public(u):
    """Ce are voie sa vada interfata despre un cont (fara hash)."""
    return {"user": u["user"], "role": u["role"],
            "cameras": u.get("cameras"), "groups": u.get("groups"),
            "totp": bool(u.get("totp"))}


def upsert(name, password=None, role=None, cameras=None, groups=None):
    """Creeaza sau modifica un cont. Parola goala la un cont existent = neschimbata.
    Refuza sa lase sistemul fara niciun administrator."""
    name = (name or "").strip()[:40]
    if not name or not name.replace("-", "").replace("_", "").replace(".", "").isalnum():
        raise ValueError("nume de utilizator invalid (litere, cifre, - _ .)")
    with _lock:      # tot RMW-ul sub lock: doua modificari concurente nu se mai pierd
        lst = load()
        u = next((x for x in lst if x["user"] == name), None)
        if u is None:
            if not password or len(password) < 6:
                raise ValueError("parola trebuie să aibă minim 6 caractere")
            u = {"user": name}
            lst.append(u)
        elif password:
            if len(password) < 6:
                raise ValueError("parola trebuie să aibă minim 6 caractere")
        if password:
            if u.get("hash"):          # parola SCHIMBATA: sesiunile vechi mor peste tot
                u["sess_ver"] = sess_ver(u) + 1
            u["hash"] = generate_password_hash(password)
        if role is not None:
            if role not in ROLES:
                raise ValueError(f"rol necunoscut: {role}")
            u["role"] = role
        u.setdefault("role", "viewer")
        if cameras is not None:
            u["cameras"] = sorted({int(i) for i in cameras}) or None
        if groups is not None:
            u["groups"] = sorted({str(g).strip()[:30] for g in groups if str(g).strip()}) or None
        if not any(x.get("role") == "admin" for x in lst):
            raise ValueError("trebuie să ramana cel puțin un administrator")
        _write(lst)
    return u


def delete(name):
    with _lock:
        lst = load()
        rest = [u for u in lst if u["user"] != name]
        if len(rest) == len(lst):
            raise ValueError("utilizatorul nu există")
        if not any(u.get("role") == "admin" for u in rest):
            raise ValueError("nu poți șterge ultimul administrator")
        _write(rest)


def allowed_ids(user, cams):
    """Camerele vizibile pentru un cont: None = toate (admin sau viewer nerestrans),
    altfel multimea id-urilor permise (camere alese direct + cele din grupurile alese)."""
    if not user or user.get("role") == "admin":
        return None
    ids, groups = user.get("cameras"), user.get("groups")
    if not ids and not groups:
        return None
    out = set(ids or [])
    if groups:
        out |= {c["id"] for c in cams if (c.get("group") or "Altele") in groups}
    return out


# ---- sesiuni revocabile ----
def sess_ver(u):
    return int((u or {}).get("sess_ver") or 0)


def revoke(name):
    """Incrementeaza versiunea sesiunilor contului: toate cookie-urile lui devin invalide
    (current_user compara versiunea din cookie cu cea din fisier). Intoarce versiunea noua."""
    with _lock:
        lst = load()
        u = next((x for x in lst if x["user"] == name), None)
        if u is None:
            raise ValueError("utilizatorul nu există")
        u["sess_ver"] = sess_ver(u) + 1
        _write(lst)
        return u["sess_ver"]


# ---- autentificare in doi pasi (TOTP) ----
def set_totp(name, secret, coduri_recuperare):
    """Activeaza 2FA: secretul + codurile de recuperare (hash-uite; cele in clar se arata
    o singura data). Sesiunile existente raman valabile — doar login-urile viitoare cer cod."""
    with _lock:
        lst = load()
        u = next((x for x in lst if x["user"] == name), None)
        if u is None:
            raise ValueError("utilizatorul nu există")
        u["totp"] = {"secret": secret, "ultimul": 0,
                     "recuperare": [generate_password_hash(c) for c in coduri_recuperare]}
        _write(lst)
        return u


def clear_totp(name):
    with _lock:
        lst = load()
        u = next((x for x in lst if x["user"] == name), None)
        if u is None:
            raise ValueError("utilizatorul nu există")
        avea = bool(u.pop("totp", None))
        _write(lst)
        return avea


def totp_folosit(name, contor):
    """Memoreaza contorul pasului acceptat (anti-replay); False daca e deja folosit."""
    with _lock:
        lst = load()
        u = next((x for x in lst if x["user"] == name), None)
        t = (u or {}).get("totp")
        if not t or contor <= int(t.get("ultimul") or 0):
            return False
        t["ultimul"] = contor
        _write(lst)
        return True


def recuperare_folosita(name, cod):
    """Consuma un cod de recuperare: True daca era valid (si acum nu mai e)."""
    cod = (cod or "").strip().lower()
    if not cod:
        return False
    with _lock:
        lst = load()
        u = next((x for x in lst if x["user"] == name), None)
        t = (u or {}).get("totp")
        if not t:
            return False
        for i, h in enumerate(t.get("recuperare") or []):
            if check_password_hash(h, cod):
                del t["recuperare"][i]
                _write(lst)
                return True
    return False
