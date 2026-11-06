#!/usr/bin/env python3
"""Escaladarea alertelor critice către un serviciu extern de notificări, printr-un
`POST /v1/fire` (contractul: `{"type": ..., "context": {...}}`, antet `X-API-Key`). Serviciul
decide canalele (Telegram, SMS, email…), throttling-ul și retry-ul — noi doar „tragem".

Decuplat și opțional: dacă setarea `alert_service_key` e goală, nu facem nimic. Niciodată nu
ridică și nu blochează (best-effort, timeout scurt, în fir) — o regulă critică (ex. căderea unei
persoane) nu are voie să cadă fiindcă escaladarea nu răspunde; alerta primară a NVR-ului pleacă
oricum pe canalele proprii. Vezi docs/reguli-analiza.md.
"""
import threading

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
