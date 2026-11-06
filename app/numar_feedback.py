#!/usr/bin/env python3
"""Butoanele ✓ ✕ sub notificarile de NUMAR de inmatriculare (🚗 <placa>) de pe Telegram.

Verdictul priveste CITIREA (OCR), nu masina: „corect" / „gresit". Semantica:
  ✓ corect -> doar semnal de incredere;
  ✕ gresit -> citirea a fost gresita: randul din jurnalul de citiri (data/plates/log.jsonl)
              iese (nu mai conteaza ca o trecere reala) si misread-ul se logheaza.
Corectia prin TASTARE a placii nu incape pe butoane inline — ramane in UI (Setari -> Detectie).

callback_data (max 64 octeti): `nv|<act>|<cam>|<zi fara liniute>|<HHMMSS>|<placa>`. Din ele se
reface calea crop-ului salvat de plates (crops/<zi>/<HHMMSS>_<placa>.jpg), cheia randului din jurnal.
"""
import re

from i18n import t

PREFIX = "nv"
_PLACA = r"[A-Z0-9]{4,10}"           # exact plates.normalize
_CROP = re.compile(r"^crops/(\d{4}-\d{2}-\d{2})/(\d{6})_([A-Z0-9]{4,10})\.jpg$")


def date(act, cam, zi, hms, placa):
    return f"{PREFIX}|{act}|{int(cam)}|{zi.replace('-', '')}|{hms}|{placa}"[:64]


def din_crop(cam, crop, placa):
    """(zi, HHMMSS) din calea crop-ului plates (crops/<zi>/<HHMMSS>_<placa>.jpg), sau None
    daca lipseste / nu se potriveste placa."""
    m = _CROP.match(crop or "")
    if not m or m[3] != placa:
        return None
    return m[1], m[2]


def taste(cam, zi, hms, placa):
    d = lambda act: date(act, cam, zi, hms, placa)   # noqa: E731
    return {"inline_keyboard": [[
        {"text": "✓ " + t("Corect"), "callback_data": d("ok")},
        {"text": "✕ " + t("Greșit"), "callback_data": d("nu")}]]}


def _parseaza(data):
    p = (data or "").split("|")
    if len(p) != 6 or p[0] != PREFIX or not p[2].isdigit() \
            or not re.fullmatch(r"\d{8}", p[3]) or not re.fullmatch(r"\d{6}", p[4]) \
            or not re.fullmatch(_PLACA, p[5]):
        return None
    zi = f"{p[3][:4]}-{p[3][4:6]}-{p[3][6:]}"
    return p[1], int(p[2]), zi, p[4], p[5]


def e_al_nostru(data):
    return str(data or "").startswith(PREFIX + "|")


def callback(cq, cfg):
    """Un buton `nv|…` apasat (chat-ul deja verificat). Textul scurt pentru answerCallbackQuery."""
    import plates
    import scena_web
    p = _parseaza(cq.get("data"))
    if not p:
        return None
    act, cam, zi, hms, placa = p
    msg = cq.get("message") or {}
    cine = "telegram:" + ((cq.get("from") or {}).get("first_name") or "?")
    om = cine.split(":", 1)[1]
    crop = f"crops/{zi}/{hms}_{placa}.jpg"
    if act == "ok":
        plates.feedback(cam, placa, "ok", crop=crop, cine=cine)
        scena_web._verdict_pe_mesaj(cfg, msg, t("✓ corect ({cine})", cine=om))
        return t("Mulțumesc, notat.")
    if act == "nu":
        r = plates.feedback(cam, placa, "gresit", crop=crop, cine=cine)
        scena_web._verdict_pe_mesaj(cfg, msg, t("✕ greșit, scos din jurnal ({cine})", cine=om))
        return t("Notat: scos din jurnal.") if r.get("scos") else t("Notat.")
    return None
