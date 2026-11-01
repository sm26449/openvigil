#!/usr/bin/env python3
"""Butoanele ✓ ✕ ∅ sub notificarile de detectie YOLO de pe Telegram (🚨 om / caine / pisica / urs,
👤 fata, 🚗 numar primesc doar textul; aici e detectia pe eticheta).

Detectiile vin pe alt drum decat „Invata scena” (detect.py -> notify.detection, cu cadrul
adnotat), dar verdictul omului ajunge in acelasi feedback.jsonl: decupajul din chenarul
detectiei (sidecarul <poza>.json al snapshotului, scris de snaps.save_frame) e salvat in
data/scena/<cam>/, ca orice candidat, deci intra in exemplele invatate ale camerei (✓ om =
exemplu „person”, ∅ = exemplu „nimic”), in datasetul de fine-tune si in recomandarile de prag.

callback_data (max 64 octeti): `dt|<act>|<cam>|<zi fara liniute>|<HHMMSS>|<eticheta>` — numele
snapshotului se reface din ele (<HHMMSS>_cam<cam>_<eticheta>.jpg).
"""
import json
import os
import re
import time
import zlib

import logare
from i18n import t

PREFIX = "dt"
MARGINE = 0.03          # chenarul desenat de YOLO ramane in afara decupajului
_STEM = re.compile(r"^(\d{6})_cam(\d+)_([\w-]+)\.jpg$")


def date(act, cam, zi, hms, eticheta):
    return f"{PREFIX}|{act}|{int(cam)}|{zi.replace('-', '')}|{hms}|{eticheta}"[:64]


def din_poza(cam, poza):
    """(zi, hms, eticheta) din perechea (zi, fisier) data de snaps.save_frame, sau None."""
    if not poza:
        return None
    zi, fn = poza
    m = _STEM.match(fn or "")
    if not m or int(m[2]) != int(cam):
        return None
    return zi, m[1], m[3]


def taste(cam, zi, hms, eticheta):
    d = lambda act: date(act, cam, zi, hms, eticheta)   # noqa: E731
    return {"inline_keyboard": [[
        {"text": "✓ " + t("Corect"), "callback_data": d("ok")},
        {"text": "✕ " + t("Nu e asta"), "callback_data": d("nu")},
        {"text": "∅ " + t("Fals, nu e nimic"), "callback_data": d("nm")}]]}


def _parseaza(data):
    p = (data or "").split("|")
    if len(p) != 6 or p[0] != PREFIX or not p[2].isdigit() or not re.fullmatch(r"\d{8}", p[3]) \
            or not re.fullmatch(r"\d{6}", p[4]) or not re.fullmatch(r"[\w-]+", p[5]):
        return None
    zi = f"{p[3][:4]}-{p[3][4:6]}-{p[3][6:]}"
    return p[1], int(p[2]), zi, p[4], p[5]


def _snapshot(cam, zi, hms, eticheta):
    import snaps
    fn = f"{hms}_cam{int(cam)}_{eticheta}.jpg"
    return os.path.join(snaps.SNAP_DIR, zi, fn), f"{zi}/{fn}"


def _chenar(cale, eticheta):
    """Chenarul cel mai sigur al etichetei din sidecar: [x, y, w, h] normalizat + incredere."""
    try:
        with open(cale + ".json", encoding="utf-8") as f:
            boxes = (json.load(f) or {}).get("boxes") or []
    except (OSError, ValueError):
        return None, None
    ale = [b for b in boxes if b.get("cls") == eticheta] or boxes
    if not ale:
        return None, None
    b = max(ale, key=lambda x: float(x.get("conf") or 0))
    return [float(b["x"]), float(b["y"]), float(b["w"]), float(b["h"])], float(b.get("conf") or 0)


def decupaj(cam, zi, hms, eticheta):
    """Decupajul chenarului, salvat in data/scena/<cam>/ cu nume de candidat
    (<YYYYMMDD>-<HHMMSS>-<n>.jpg, n = 9xxxxx din eticheta). (nume fisier, chenar, incredere, cadru)
    sau (None, None, None, cadru) daca nu exista snapshot / sidecar."""
    cale, cadru = _snapshot(cam, zi, hms, eticheta)
    if not os.path.isfile(cale):
        return None, None, None, None
    chenar, conf = _chenar(cale, eticheta)
    if not chenar:
        return None, None, None, cadru
    import scena_web
    from PIL import Image
    img = Image.open(cale).convert("RGB")
    W, H = img.size
    x, y, w, h = chenar
    mx, my = w * MARGINE, h * MARGINE
    box = (int((x + mx) * W), int((y + my) * H), int((x + w - mx) * W), int((y + h - my) * H))
    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
        return None, chenar, conf, cadru
    fn = f"{zi.replace('-', '')}-{hms}-9{zlib.crc32(eticheta.encode()) % 100000:05d}.jpg"
    d = os.path.join(scena_web.SCENA_DIR, str(int(cam)))
    os.makedirs(d, exist_ok=True)
    img.crop(box).save(os.path.join(d, fn), "JPEG", quality=90)
    return fn, chenar, conf, cadru


def verdict(cam, zi, hms, eticheta, verdict, nume=None, cine=None):
    """Verdictul omului pe o detectie: rand in feedback.jsonl (cu decupajul, daca se poate)."""
    import scena_web
    eid = f"d-{zi.replace('-', '')}-{hms}-{int(cam)}-{eticheta}"[:32]
    fn, chenar, conf, cadru = decupaj(cam, zi, hms, eticheta)
    initial = {"eticheta_initiala": eticheta, "sursa_initiala": "yolo", "cadru": cadru}
    if conf is not None:
        initial["incredere"] = round(conf, 3)
    if chenar:
        initial["regiune"] = [round(v, 4) for v in chenar]
    r = scena_web.feedback_adauga(cam, eid, verdict, eticheta=nume, poza=fn, cine=cine, sursa="detectie", initial=initial)
    logare.info(f"[detectie] verdict cam{cam} {eticheta} {hms}: {verdict} {nume or ''} {'(fara decupaj)' if not fn else ''}".rstrip())
    return r


def callback(cq, cfg):
    """Un buton `dt|…` apasat (chat-ul deja verificat). Textul scurt pentru answerCallbackQuery."""
    import scena_web
    p = _parseaza(cq.get("data"))
    if not p:
        return None
    act, cam, zi, hms, eticheta = p
    msg = cq.get("message") or {}
    cine = "telegram:" + ((cq.get("from") or {}).get("first_name") or "?")
    om = cine.split(":", 1)[1]
    d = lambda a: date(a, cam, zi, hms, eticheta)   # noqa: E731
    if act == "ok":
        verdict(cam, zi, hms, eticheta, "ok", cine=cine)
        scena_web._verdict_pe_mesaj(cfg, msg, t("✓ corect ({cine})", cine=om))
        return t("Mulțumesc, notat.")
    if act == "nm":
        verdict(cam, zi, hms, eticheta, "gresit", nume=scena_web.NIMIC, cine=cine)
        scena_web._verdict_pe_mesaj(cfg, msg, t("∅ fals, nu era nimic ({cine})", cine=om))
        return t("Notat: fals.")
    if act == "ns":
        verdict(cam, zi, hms, eticheta, "necunoscut", cine=cine)
        scena_web._verdict_pe_mesaj(cfg, msg, t("? nu se vede clar ({cine})", cine=om))
        return t("Notat: nu se vede clar.")
    if act == "nu":
        scena_web._tg(cfg, "editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
                      reply_markup=scena_web._taste_vocabular(cam, None, None, date=d))
        return t("Ce e de fapt?")
    if act == "inapoi":
        scena_web._tg(cfg, "editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
                      reply_markup=taste(cam, zi, hms, eticheta))
        return None
    if act.startswith("et") and act[2:].isdigit():
        voc = scena_web.vocabular()
        i = int(act[2:])
        if i >= len(voc):
            return t("Vocabularul s-a schimbat; deschide fișa camerei.")
        verdict(cam, zi, hms, eticheta, "gresit", nume=voc[i], cine=cine)
        scena_web._verdict_pe_mesaj(cfg, msg, t("✕ e {ce} ({cine})", ce=t(voc[i]), cine=om))
        return t("Notat: {ce}.", ce=t(voc[i]))
    return None


def e_al_nostru(data):
    return str(data or "").startswith(PREFIX + "|")


def ora_din(hms):
    return f"{hms[:2]}:{hms[2:4]}:{hms[4:]}" if re.fullmatch(r"\d{6}", hms or "") else time.strftime("%H:%M:%S")
