#!/usr/bin/env python3
"""Partea de web a detectiei pe schimbare („Invata scena”, motorul e scena.py in nvr-ai):

  - feedback-ul omului pe un candidat (✓ corect / ✕ e altceva / ? nu stiu), in
    data/scena/feedback.jsonl — materia prima a antrenamentului urmator;
  - candidatii pe Telegram, ca poza cu butoane inline (✓ / ✕ / 🔍), cand setarea
    `scena_telegram` e pornita: ploaia, umbrele si frunzele nu pleaca (NEUTRE), „necunoscut”
    pleaca doar ziua si pe vreme buna (noaptea e granulatie IR, pe ploaie sunt picaturi),
    noaptea pleaca doar etichetele date de un detector cu incredere de cel putin
    NOAPTE_INCREDERE, iar o camera nu trimite mai des de `scena_telegram_cooldown` secunde;
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
NOAPTE_INCREDERE = 0.5   # noaptea pleaca pe Telegram doar etichetele cu cel putin atata incredere (implicit;
                         # setarea `scena_telegram_noapte`, iar per camera `scena_noapte` din detect.json)...
SURSE_NOAPTE = {"yolo", "yolo-world", "vlm"}   # ...si doar de la un detector, nu „yolo-nesigur” / vocabular


def prag_noapte(cam):
    """Pragul de incredere al camerei noaptea: al ei (recomandare aplicata) sau cel global."""
    try:
        import detect
        p = detect.cam_cfg(int(cam)).get("scena_noapte")
    except Exception:
        p = None
    if not p:
        p = settings.get("scena_telegram_noapte")
    try:
        return float(p) if p else NOAPTE_INCREDERE
    except (TypeError, ValueError):
        return NOAPTE_INCREDERE


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
MARJA_AFISARE = 0.02     # in textul analizei, numele din vocabular la mai mult de atat sub primul nu se arata


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
        if ev.get("incredere") is not None:
            r["incredere"] = ev.get("incredere")
        if isinstance(ev.get("regiune"), list):
            r["regiune"] = ev["regiune"]
        if ev.get("vreme"):
            r["vreme"] = ev["vreme"]
        if not r["poza"] and ev.get("poza"):
            r["poza"] = ev["poza"]
    os.makedirs(SCENA_DIR, exist_ok=True)
    with _fb_lock, open(FEEDBACK, "a", encoding="utf-8") as f:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if verdict == "gresit" and r["eticheta"] and r["poza"]:
        _index_eticheta(r["cam"], r["poza"], r["eticheta"])
    if r["poza"]:
        _fundal_invata(r["cam"], r["poza"], r)
    return r


def _fundal_invata(cam, poza, rand):
    """Exemplele invatate ale camerei afla verdictul: un nume (fundal sau obiect) adauga exemplul,
    lipsa lui („?”, necunoscut) il scoate daca era. Niciodata nu arunca."""
    try:
        import ai_client
        import scena_fundal
        nume = scena_fundal.nume_exemplu(rand)
        if ai_client.remote():
            ai_client.scena_fundal(cam, poza, nume)
        elif not settings.get("ai_service"):
            if nume:
                scena_fundal.adauga(cam, poza, nume)
            else:
                scena_fundal.scoate(cam, poza)
    except Exception as e:
        logare.info(f"[scena] fundal invatat: {str(e)[:80]}")


def _index_eticheta(cam, poza, eticheta):
    """Indexul semantic afla verdictul (eticheta noua, sau randul iese); niciodata nu arunca."""
    try:
        import ai_client
        import semantic
        if ai_client.remote():
            ai_client.semantic_scena(cam, poza, eticheta)
        elif semantic.IS_HOST or not settings.get("ai_service"):
            semantic.actualizeaza_scena(cam, poza, eticheta)
    except Exception as e:
        logare.info(f"[scena] index: {str(e)[:80]}")


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
    cam = int(entry["cam"])
    eticheta = entry.get("eticheta") or "necunoscut"
    noapte = _e_noapte()
    if eticheta == "necunoscut":
        if noapte:
            logare.info(f"[scena] telegram cam{cam}: sarit, necunoscut noaptea (granulație IR)")
            return False
        if not settings.get("scena_telegram_ploaie"):
            try:
                import vreme
                if vreme.acum().get("rea"):
                    return False         # pe ploaie/ceata, necunoscutele sunt picaturi: nu deranjam omul
            except Exception:
                pass
    elif noapte and (entry.get("sursa") not in SURSE_NOAPTE or float(entry.get("incredere") or 0) < prag_noapte(cam)):
        logare.info(f"[scena] telegram cam{cam}: sarit, noaptea {eticheta} ({entry.get('sursa')}, "
                    f"{float(entry.get('incredere') or 0):.2f}) e sub pragul {prag_noapte(cam)}")
        return False
    now = now or time.time()
    cd = int(settings.get("scena_telegram_cooldown") or 0)
    with _ultim_lock:
        de_cand = now - _ultim.get(cam, 0)
        if de_cand < cd:
            logare.info(f"[scena] telegram cam{cam}: sarit, ultimul acum {int(de_cand)} s (cooldown {cd} s)")
            return False
        _ultim[cam] = now
    logare.info(f"[scena] telegram cam{cam}: trimit {entry.get('eticheta')} ({entry.get('id')})")
    return True


def curata(zile=None):
    """Sterge decupajele scenei mai vechi de `zile` (implicit retentia pozelor), in afara
    celor cu verdict in feedback.jsonl: acelea sunt datasetul urmator. Intoarce cate a sters."""
    zile = int(zile or settings.get("retention_snapshots_days") or 90)
    pastrate = {(r["cam"], r.get("poza")) for r in feedback_citeste().values() if r.get("poza")}
    limita = time.time() - zile * 86400
    sterse = 0
    try:
        camere = [d for d in os.listdir(SCENA_DIR) if d.isdigit()]
    except FileNotFoundError:
        return 0
    for d in camere:
        cale = os.path.join(SCENA_DIR, d)
        for fn in os.listdir(cale):
            if not fn.endswith(".jpg") or fn == "fundal.jpg" or (int(d), fn) in pastrate:
                continue
            try:
                if os.path.getmtime(os.path.join(cale, fn)) < limita:
                    os.remove(os.path.join(cale, fn))
                    sterse += 1
            except OSError:
                pass
    if sterse:
        logare.info(f"[scena] curatare: {sterse} decupaje mai vechi de {zile} zile sterse")
    return sterse


def _e_noapte():
    """Noapte acum (apus/rasarit cand e locatia setata, altfel orele din Setari); niciodata nu arunca."""
    try:
        import rules
        return bool(rules._is_night())
    except Exception:
        return False


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
        # scorurile SigLIP stau intr-o banda ingusta: aratam doar numele care ies in fata
        # (peste prag si aproape de primul), nu trei nume oricum, dintre care doua sunt zgomot
        import scena_clasa
        varf = v[0]["scor"]
        clare = [x for x in v if x["scor"] >= scena_clasa.PRAG_VOCAB and x["scor"] >= varf - MARJA_AFISARE]
        if clare:
            parti.append(t("vocabular: {lista}", lista=", ".join(f"{t(x['nume'])} {round(x['scor'] * 1000)}" for x in clare[:3])))
        else:
            parti.append(t("vocabular: nimic clar (cel mai apropiat: {nume} {scor})", nume=t(v[0]["nume"]), scor=round(varf * 1000)))
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
