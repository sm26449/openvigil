#!/usr/bin/env python3
"""Salvarea si refacerea configuratiei.

Ce se salveaza: registrul de camere (cu parole — e o copie de siguranta, deci fisierul
e sensibil), setarile, detectia si zonele, utilizatorii (cu hash-uri, nu parole),
regulile si masinile cunoscute. NU se salveaza inregistrarile, pozele sau biblioteca
de fete (sunt date, nu configuratie).
"""
import json
import os
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = {
    "cameras": os.path.join(ROOT, "data", "cameras.json"),
    "settings": os.path.join(ROOT, "data", "settings.json"),
    "detect": os.path.join(ROOT, "data", "detect.json"),
    "users": os.path.join(ROOT, "data", "users.json"),
    "rules": os.path.join(ROOT, "data", "rules.json"),
    "plates_known": os.path.join(ROOT, "data", "plates", "known.json"),
}
# Tot ce e secret in settings — lista se completeaza ODATA cu fiecare functie noua,
# altfel varianta "fara parole" scurge jetoane (s-a intamplat: api_token si
# webhook_secret au stat expuse pana la auditul din 2026-08-14).
SETTINGS_SECRETS = ("web_password", "telegram_token", "email_pass",
                    "api_token", "webhook_secret", "mqtt_pass")


def export_config(include_secrets=True):
    out = {"versiune": 2, "creat": time.strftime("%Y-%m-%dT%H:%M:%S"), "date": {}}
    for name, path in FILES.items():
        try:
            with open(path) as f:
                data = json.load(f)
        except (FileNotFoundError, ValueError):
            continue
        if not include_secrets:
            if name == "cameras":
                data = [{k: v for k, v in c.items() if k != "pass"} for c in data]
            elif name == "settings":
                data = {k: v for k, v in data.items() if k not in SETTINGS_SECRETS}
            elif name == "users":
                data = [{k: v for k, v in u.items() if k != "hash"} for u in data]
        out["date"][name] = data
    out["fara_parole"] = not include_secrets
    return out


def import_config(payload):
    """Reface configuratia dintr-un fisier exportat. Face intai o copie a starii curente."""
    if not isinstance(payload, dict) or "date" not in payload:
        raise ValueError("fișier de configurație nevalid")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    restored = []
    for name, path in FILES.items():
        if name not in payload["date"]:
            continue
        if name == "users" and any("hash" not in u
                                   for u in payload["date"]["users"] or [{}]):
            continue   # un export "fara parole" nu poate reface conturile
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):       # copie de siguranta a starii curente
            os.replace(path, f"{path}.inainte-de-{stamp}")
        import store
        store.atomic_write_json(path, payload["date"][name], mode=0o600, indent=2)
        restored.append(name)
    return restored
