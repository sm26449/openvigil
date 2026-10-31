#!/usr/bin/env python3
"""Partea de web a detectiei pe schimbare („Invata scena”, motorul e scena.py in nvr-ai):

  - feedback-ul omului pe un candidat (✓ corect / ✕ e altceva / ? nu stiu), in
    data/scena/feedback.jsonl — materia prima a antrenamentului urmator;
  - candidatii pe Telegram, ca poza cu butoane inline (✓ / ✕ / 🔍), cand setarea
    `scena_telegram` e pornita: ploaia, umbrele si frunzele nu pleaca (NEUTRE), iar o camera
    nu trimite mai des de `scena_telegram_cooldown` secunde;
  - apasarea butoanelor (callback_query, citit de telegram_bot): ✕ deschide vocabularul ca
    butoane, 🔍 cere analiza adanca din nvr-ai (YOLO 640 + vocabular + VLM) si raspunde cu
    parerea ei, din nou cu butoane. Tot ce apasa omul ajunge in acelasi feedback.jsonl.

callback_data (Telegram: cel mult 64 de octeti): `sc|<actiune>|<cam>|<id>|<poza fara .jpg>`;
actiunea e ok / nu / ns / nm (nimic nou, doar fundal) / inapoi / re / okA (acceptarea
analizei) / et<index in vocabular>.
"""
import json
import os
import threading
import time

import requests

import logare
import settings
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCENA_DIR = os.path.join(ROOT, "data", "scena")
FEEDBACK = os.path.join(SCENA_DIR, "feedback.jsonl")
NIMIC = "nimic"          # verdictul „e doar fundalul”: intra in dataset ca negativ (feedback_dataset)


def _neutre():
    """Numele care nu merita un mesaj (vreme, fundal) — din scena_clasa; „necunoscut” PLEACA:
    exact pe el vrem verdictul omului."""
    import scena_clasa
    return scena_clasa.NEUTRE - {"necunoscut"}
VERDICTE = ("ok", "gresit", "necunoscut")

_fb_lock = threading.Lock()
_ultim = {}                 # cam -> cand a plecat ultimul candidat pe Telegram
_ultim_lock = threading.Lock()
_analize = {}               # (cam, id) -> eticheta propusa de analiza (pentru butonul ✓ al ei)
_ETICHETA_MAX = 60


# ---- feedback ----
def feedback_adauga(cam, eid, verdict, eticheta=None, poza=None, cine=None, sursa=None):
    """Un rand in feedback.jsonl; intoarce randul. verdict in VERDICTE, altfel ValueError."""
    if verdict not in VERDICTE:
        raise ValueError("verdict")
    r = {"cam": int(cam), "id": str(eid)[:32], "verdict": verdict,
         "eticheta": (str(eticheta or "")[:_ETICHETA_MAX] or None),
         "poza": (str(poza or "")[:60] or None), "cine": cine,
         "t": time.strftime("%Y-%m-%d %H:%M:%S")}
    if sursa:
        r["sursa"] = sursa
    ev = _eveniment(cam, eid)
    if ev:
        # ce propusese sistemul si unde era, ca datasetul sa stie eticheta unui „✓ corect”
        r["eticheta_initiala"] = ev.get("eticheta")
        r["sursa_initiala"] = ev.get("sursa")
        if isinstance(ev.get("regiune"), list):
            r["regiune"] = ev["regiune"]
        if ev.get("vreme"):
            r["vreme"] = ev["vreme"]
        if not r["poza"] and ev.get("poza"):
            r["poza"] = ev["poza"]
    os.makedirs(SCENA_DIR, exist_ok=True)
    with _fb_lock, open(FEEDBACK, "a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return r


def _eticheta_analiza(cam, eid):
    """Eticheta ultimei analize a candidatului, din jurnal (cand memoria procesului e goala)."""
    try:
        import jurnal
        for e in jurnal.query(limit=300, tip="scena_analiza", cam=int(cam)):
            if e.get("candidat") == eid:
                return e.get("eticheta")
    except Exception as e:
        logare.info(f"[scena] jurnal analize: {str(e)[:80]}")
    return None


def _eveniment(cam, eid):
    """Evenimentul scena_candidat cu id-ul dat (din jurnal), sau None."""
    try:
        import jurnal
        for e in jurnal.query(limit=600, tip="scena_candidat", cam=int(cam)):
            if e.get("id") == eid:
                return e
    except Exception as e:
        logare.info(f"[scena] jurnal: {str(e)[:80]}")
    return None


def feedback_citeste(cam_id=None):
    """{id eveniment: rand} din feedback.jsonl (ultimul cuvant conteaza)."""
    out = {}
    try:
        with open(FEEDBACK, encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if cam_id is None or r.get("cam") == cam_id:
                    out[r["id"]] = r
    except OSError:
        pass
    return out


def vocabular():
    try:
        import scena_clasa
        return scena_clasa.vocabular()
    except Exception:
        return [str(x) for x in (settings.get("scena_vocabular") or [])]


# ---- Telegram ----
def _tg(cfg, metoda, fisiere=None, **date):
    """Un apel la Bot API; intoarce `result` sau ridica RuntimeError cu motivul."""
    for k, v in list(date.items()):
        if isinstance(v, (dict, list)):
            date[k] = json.dumps(v, ensure_ascii=False)
    r = requests.post(f"https://api.telegram.org/bot{cfg['telegram_token']}/{metoda}",
                      data=date, files=fisiere, timeout=20)
    d = r.json()
    if not d.get("ok"):
        raise RuntimeError(d.get("description") or metoda)
    return d.get("result")


def _nume_cam(cam_id):
    import notify
    return notify._cam_name(cam_id)


def _date(act, cam, eid, poza):
    return f"sc|{act}|{int(cam)}|{eid}|{(poza or '').rsplit('.', 1)[0]}"[:64]


def _taste(cam, eid, poza):
    return {"inline_keyboard": [[
        {"text": "✓ " + t("Corect"), "callback_data": _date("ok", cam, eid, poza)},
        {"text": "✕ " + t("Nu e asta"), "callback_data": _date("nu", cam, eid, poza)},
        {"text": "🔍 " + t("Reanalizează"), "callback_data": _date("re", cam, eid, poza)}]]}


def _taste_vocabular(cam, eid, poza):
    """Vocabularul ca butoane (3 pe rand), plus „nu stiu” si inapoi."""
    rnd, taste = [], []
    for i, n in enumerate(vocabular()[:30]):
        rnd.append({"text": t(n), "callback_data": _date(f"et{i}", cam, eid, poza)})
        if len(rnd) == 3:
            taste.append(rnd)
            rnd = []
    if rnd:
        taste.append(rnd)
    taste.append([{"text": "∅ " + t("Nimic nou (doar fundal)"), "callback_data": _date("nm", cam, eid, poza)}])
    taste.append([{"text": "? " + t("Nu se vede clar"), "callback_data": _date("ns", cam, eid, poza)},
                  {"text": "↩ " + t("Înapoi"), "callback_data": _date("inapoi", cam, eid, poza)}])
    return {"inline_keyboard": taste}


def _taste_analiza(cam, eid, poza):
    return {"inline_keyboard": [[
        {"text": "✓ " + t("Da, asta e"), "callback_data": _date("okA", cam, eid, poza)},
        {"text": "✕ " + t("Nu e asta"), "callback_data": _date("nu", cam, eid, poza)}]]}


def _eticheta_text(eticheta):
    """Eticheta tradusa pentru mesaj (numele YOLO au traducerea in notify.RO)."""
    import notify
    return t(notify.RO.get(eticheta, eticheta or "necunoscut"))


def _pct(e):
    inc = e.get("incredere") or 0
    return round(inc * (1000 if e.get("sursa") == "vocabular" else 100))


def activ():
    cfg = settings.load()
    return bool(cfg.get("scena_telegram") and cfg.get("telegram_token") and cfg.get("telegram_chat"))


def de_trimis(entry, now=None):
    """Pleaca pe Telegram candidatul asta? Pornit, nu e vreme, poza exista, cooldown pe camera."""
    if not activ() or not entry.get("poza") or entry.get("cam") is None:
        return False
    if (entry.get("eticheta") or "") in _neutre():
        return False
    if (entry.get("eticheta") or "necunoscut") == "necunoscut" and not settings.get("scena_telegram_ploaie"):
        try:
            import vreme
            if vreme.acum().get("rea"):
                return False             # pe ploaie/ceata, necunoscutele sunt picaturi: nu deranjam omul
        except Exception:
            pass
    now = now or time.time()
    cd = int(settings.get("scena_telegram_cooldown") or 0)
    cam = int(entry["cam"])
    with _ultim_lock:
        de_cand = now - _ultim.get(cam, 0)
        if de_cand < cd:
            logare.info(f"[scena] telegram cam{cam}: sarit, ultimul acum {int(de_cand)} s (cooldown {cd} s)")
            return False
        _ultim[cam] = now
    logare.info(f"[scena] telegram cam{cam}: trimit {entry.get('eticheta')} ({entry.get('id')})")
    return True


def notifica(entry):
    """Chemata din rules._journal pentru fiecare `scena_candidat`: poza + butoane, in fir."""
    if not de_trimis(entry):
        return False
    cam, eid, poza = entry["cam"], entry["id"], entry["poza"]

    def work():
        try:
            with open(os.path.join(SCENA_DIR, str(cam), poza), "rb") as f:
                jpeg = f.read()
            cfg = settings.load()
            text = t("🧩 {cam}: {ce} · {pct}% · {ora}", cam=_nume_cam(cam),
                     ce=_eticheta_text(entry.get("eticheta")), pct=_pct(entry),
                     ora=(entry.get("t") or "")[11:16])
            text += "\n" + t("Învață scena: e corect?")
            _tg(cfg, "sendPhoto", fisiere={"photo": ("s.jpg", jpeg, "image/jpeg")},
                chat_id=cfg["telegram_chat"], caption=text, reply_markup=_taste(cam, eid, poza))
        except Exception as e:
            logare.info(f"[scena] telegram: {str(e)[:80]}")

    threading.Thread(target=work, daemon=True, name="scena-telegram").start()
    return True


def _parseaza(data):
    """'sc|act|cam|id|poza' -> (act, cam, id, poza.jpg) sau None daca nu e al nostru."""
    p = (data or "").split("|")
    if len(p) != 5 or p[0] != "sc" or not p[2].isdigit() or not p[3]:
        return None
    return p[1], int(p[2]), p[3], (p[4] + ".jpg") if p[4] else None


def _verdict_pe_mesaj(cfg, msg, text, taste=None):
    """Scrie verdictul sub poza si scoate (sau schimba) butoanele."""
    cap = (msg.get("caption") or "").split("\n")[0]
    _tg(cfg, "editMessageCaption", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
        caption=f"{cap}\n{text}", reply_markup=taste or {"inline_keyboard": []})


def _mesaj_text(cfg, msg, text, taste=None):
    _tg(cfg, "editMessageText", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
        text=text, reply_markup=taste or {"inline_keyboard": []})


def callback(cq, cfg):
    """Un buton apasat (callback_query, chat-ul deja verificat de telegram_bot).
    Intoarce textul scurt aratat omului (answerCallbackQuery)."""
    p = _parseaza(cq.get("data"))
    if not p:
        return None
    act, cam, eid, poza = p
    msg = cq.get("message") or {}
    cine = "telegram:" + ((cq.get("from") or {}).get("first_name") or "?")
    e_poza = bool(msg.get("caption") is not None or msg.get("photo"))
    scrie = _verdict_pe_mesaj if e_poza else _mesaj_text
    if act == "ok":
        feedback_adauga(cam, eid, "ok", poza=poza, cine=cine)
        scrie(cfg, msg, t("✓ corect ({cine})", cine=cine.split(":", 1)[1]))
        return t("Mulțumesc, notat.")
    if act == "okA":
        et = _analize.get((cam, eid)) or _eticheta_analiza(cam, eid)   # si dupa un restart al web
        feedback_adauga(cam, eid, "gresit" if et else "ok", eticheta=et, poza=poza, cine=cine, sursa="analiza")
        scrie(cfg, msg, t("✓ {ce} ({cine})", ce=_eticheta_text(et), cine=cine.split(":", 1)[1]))
        return t("Mulțumesc, notat.")
    if act == "nm":
        feedback_adauga(cam, eid, "gresit", eticheta=NIMIC, poza=poza, cine=cine)
        scrie(cfg, msg, t("∅ nimic nou, doar fundal ({cine})", cine=cine.split(":", 1)[1]))
        return t("Notat: doar fundal.")
    if act == "ns":
        feedback_adauga(cam, eid, "necunoscut", poza=poza, cine=cine)
        scrie(cfg, msg, t("? nu se vede clar ({cine})", cine=cine.split(":", 1)[1]))
        return t("Notat: nu se vede clar.")
    if act == "nu":
        _tg(cfg, "editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
            reply_markup=_taste_vocabular(cam, eid, poza))
        return t("Ce e de fapt?")
    if act == "inapoi":
        _tg(cfg, "editMessageReplyMarkup", chat_id=msg["chat"]["id"], message_id=msg["message_id"],
            reply_markup=_taste(cam, eid, poza) if e_poza else _taste_analiza(cam, eid, poza))
        return None
    if act.startswith("et") and act[2:].isdigit():
        voc = vocabular()
        i = int(act[2:])
        if i >= len(voc):
            return t("Vocabularul s-a schimbat; deschide fișa camerei.")
        feedback_adauga(cam, eid, "gresit", eticheta=voc[i], poza=poza, cine=cine)
        scrie(cfg, msg, t("✕ e {ce} ({cine})", ce=t(voc[i]), cine=cine.split(":", 1)[1]))
        return t("Notat: {ce}.", ce=t(voc[i]))
    if act == "re":
        if not poza:
            return t("Nu mai am decupajul.")
        threading.Thread(target=_reanalizeaza_telegram, args=(cfg, msg, cam, eid, poza),
                         daemon=True, name="scena-reanaliza").start()
        return t("Analizez… răspund într-un minut.")
    return None


def reanalizeaza(cam, eid, poza):
    """Analiza adanca in nvr-ai; intoarce rezultatul sau ridica RuntimeError."""
    import ai_client
    r = ai_client.scena_reanalizeaza(int(cam), eid, poza)
    if not r or r.get("error"):
        raise RuntimeError((r or {}).get("error") or t("nvr-ai nu răspunde"))
    if r.get("eticheta"):
        _analize[(int(cam), eid)] = r["eticheta"]
    return r


def text_analiza(r):
    """Rezultatul analizei, pe scurt, pentru om (Telegram si fisa camerei)."""
    parti = [t("🔍 Analiză: {ce}", ce=_eticheta_text(r.get("eticheta")))]
    if r.get("vlm"):
        parti.append(t("VLM: „{txt}”", txt=r["vlm"]) + (" " + t("(neconfirmat de alt model)") if r.get("vlm_neconfirmat") else ""))
    y = r.get("yolo")
    if y:
        parti.append(f"YOLO: {_eticheta_text(y.get('eticheta'))} {round((y.get('incredere') or 0) * 100)}%")
    w = r.get("lume")
    if w:
        parti.append(f"YOLO-World: {t(w.get('eticheta') or '')} {round((w.get('incredere') or 0) * 100)}%")
    v = r.get("vocab") or []
    if v:
        parti.append(t("vocabular: {lista}", lista=", ".join(f"{t(x['nume'])} {round(x['scor'] * 1000)}" for x in v[:3])))
    return "\n".join(parti)


def _reanalizeaza_telegram(cfg, msg, cam, eid, poza):
    try:
        r = reanalizeaza(cam, eid, poza)
        _tg(cfg, "sendMessage", chat_id=msg["chat"]["id"], text=text_analiza(r),
            reply_to_message_id=msg.get("message_id"), reply_markup=_taste_analiza(cam, eid, poza))
    except Exception as e:
        try:
            _tg(cfg, "sendMessage", chat_id=msg["chat"]["id"],
                text=t("🔍 Analiza nu a reușit: {e}", e=str(e)[:80]),
                reply_to_message_id=msg.get("message_id"))
        except Exception as e2:
            logare.info(f"[scena] telegram analiza: {str(e2)[:80]}")
