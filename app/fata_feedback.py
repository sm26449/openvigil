#!/usr/bin/env python3
"""Butoanele ✓ ✕ sub notificarile de FATA recunoscuta (👤 <nume>) de pe Telegram.

Spre deosebire de detectia pe eticheta (detectie_feedback) sau de „Invata scena", aici
verdictul priveste RECUNOASTEREA, nu clasa: „e el" / „nu e el". Semantica:
  ✓ e <nume>  -> doar semnal de incredere (pentru reglajul pragului de similaritate);
  ✕ nu e el   -> recunoasterea a fost falsa: crop-ul fetei salvat la notificare
                 (faces.save_recognized, cheie cam+timp) merge in data/faces/unknown/
                 pentru re-etichetare — fata ramane pentru re-ID, dar fara eticheta gresita.

callback_data (max 64 octeti): `fv|<act>|<cam>|<zi fara liniute>|<HHMMSS>|<nume>` — numele e
cheia din biblioteca (faces.sanitize_name: ASCII mic [a-z0-9-]), deci intra neschimbat.
"""
import re

import logare
from i18n import t

PREFIX = "fv"
_NUME = r"[a-z][a-z0-9-]{1,19}"      # exact faces.sanitize_name


def date(act, cam, zi, hms, nume):
    return f"{PREFIX}|{act}|{int(cam)}|{zi.replace('-', '')}|{hms}|{nume}"[:64]


def taste(cam, zi, hms, nume):
    d = lambda act: date(act, cam, zi, hms, nume)   # noqa: E731
    return {"inline_keyboard": [[
        {"text": "✓ " + t("Da, e {nume}", nume=nume), "callback_data": d("ok")},
        {"text": "✕ " + t("Nu e {nume}", nume=nume), "callback_data": d("nu")}]]}


def _parseaza(data):
    p = (data or "").split("|")
    if len(p) != 6 or p[0] != PREFIX or not p[2].isdigit() \
            or not re.fullmatch(r"\d{8}", p[3]) or not re.fullmatch(r"\d{6}", p[4]) \
            or not re.fullmatch(_NUME, p[5]):
        return None
    zi = f"{p[3][:4]}-{p[3][4:6]}-{p[3][6:]}"
    return p[1], int(p[2]), zi, p[4], p[5]


def e_al_nostru(data):
    return str(data or "").startswith(PREFIX + "|")


def callback(cq, cfg):
    """Un buton `fv|…` apasat (chat-ul deja verificat). Textul scurt pentru answerCallbackQuery."""
    import faces
    import scena_web
    p = _parseaza(cq.get("data"))
    if not p:
        return None
    act, cam, zi, hms, nume = p
    msg = cq.get("message") or {}
    cine = "telegram:" + ((cq.get("from") or {}).get("first_name") or "?")
    om = cine.split(":", 1)[1]
    if act == "ok":
        faces.feedback(cam, nume, "ok", zi, hms, cine=cine)
        scena_web._verdict_pe_mesaj(cfg, msg, t("✓ e {nume} ({cine})", nume=nume, cine=om))
        return t("Mulțumesc, notat.")
    if act == "nu":
        r = faces.feedback(cam, nume, "gresit", zi, hms, cine=cine)
        scena_web._verdict_pe_mesaj(cfg, msg, t("✕ nu e {nume} ({cine})", nume=nume, cine=om))
        logare.info(f"[fata] verdict cam{cam} {nume}: gresit{' -> unknown' if r.get('mutat') else ''}")
        return t("Notat: pus la reetichetare.") if r.get("mutat") else t("Notat.")
    return None
