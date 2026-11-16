#!/usr/bin/env python3
"""Notificari Telegram + email (text + poza) cu throttle, si watchdog pentru camerele
standalone. Configurabile din UI (tabul Setari -> data/settings.json); dezactivate cat
timp nu sunt completate. Watchdog: verifica la 60s camerele si anunta cazut/revenit."""
import json
import os
import smtplib
import threading
import time
from email.message import EmailMessage

import requests
from requests.auth import HTTPDigestAuth

import settings
import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_FILE = os.path.join(ROOT, "data", "cameras.json")

RO = {"person": "Om", "bird": "Pasare", "cat": "Pisica", "dog": "Caine",
      "horse": "Cal", "sheep": "Oaie", "cow": "Vaca", "bear": "Urs",
      "chicken": "Găină", "pigeon": "Porumbel", "car": "Mașină",
      "truck": "Camion", "bus": "Autobuz", "motorcycle": "Motocicleta"}

_last = {}
_lock = threading.Lock()


def _throttle_ok(key, cooldown):
    with _lock:
        now = time.time()
        if now - _last.get(key, 0) < cooldown:
            return False
        _last[key] = now
        return True


def _send_telegram(cfg, text, jpeg=None, taste=None):
    base = f"https://api.telegram.org/bot{cfg['telegram_token']}"
    extra = {"reply_markup": json.dumps(taste, ensure_ascii=False)} if taste else {}
    if jpeg:
        r = requests.post(f"{base}/sendPhoto",
                          data={"chat_id": cfg["telegram_chat"], "caption": text, **extra},
                          files={"photo": ("s.jpg", jpeg, "image/jpeg")}, timeout=20)
    else:
        r = requests.post(f"{base}/sendMessage",
                          data={"chat_id": cfg["telegram_chat"], "text": text, **extra}, timeout=20)
    r.raise_for_status()
    if not r.json().get("ok"):
        raise RuntimeError(r.json().get("description", t("telegram: răspuns ne-ok")))


def _send_email(cfg, text, jpeg=None):
    msg = EmailMessage()
    msg["Subject"] = f"NVR: {text[:80]}"
    msg["From"] = cfg["email_user"]
    msg["To"] = cfg["email_to"]
    msg.set_content(text)
    if jpeg:
        msg.add_attachment(jpeg, maintype="image", subtype="jpeg", filename="snapshot.jpg")
    with smtplib.SMTP(cfg["email_smtp"], int(cfg["email_port"]), timeout=20) as s:
        s.starttls()
        if cfg["email_user"]:
            s.login(cfg["email_user"], cfg["email_pass"])
        s.send_message(msg)


def send(text, jpeg=None, key=None, sync=False, taste=None):
    """Trimite pe toate canalele configurate. key = throttle per subiect.
    sync=True -> ruleaza in fir si ridica exceptii (pentru butoanele de test).
    taste = tastatura inline pentru Telegram (butoanele de verdict); emailul o ignora."""
    cfg = settings.load()
    tg = bool(cfg["telegram_token"] and cfg["telegram_chat"])
    em = bool(cfg["email_enabled"] and cfg["email_smtp"] and cfg["email_to"])
    if not (tg or em):
        if sync:
            raise RuntimeError(t("niciun canal configurat (Telegram sau email)"))
        return
    if key and not _throttle_ok(key, cfg["notify_cooldown"]):
        return

    def work():
        if tg:
            _send_telegram(cfg, text, jpeg, taste)
        if em:
            _send_email(cfg, text, jpeg)

    if sync:
        work()
    else:
        def safe():
            try:
                work()
            except Exception as e:
                logare.info(f"[notify] {e}")
        threading.Thread(target=safe, daemon=True).start()


def test_channel(channel):
    """Trimite un mesaj de test pe un canal anume; ridica exceptie cu motivul la esec."""
    cfg = settings.load()
    text = t("✅ Test notificare NVR ({ora})", ora=time.strftime('%H:%M:%S'))
    if channel == "telegram":
        if not (cfg["telegram_token"] and cfg["telegram_chat"]):
            raise RuntimeError(t("completează token-ul și chat ID-ul întâi"))
        _send_telegram(cfg, text)
    else:
        if not (cfg["email_smtp"] and cfg["email_to"]):
            raise RuntimeError(t("completează serverul SMTP și adresa destinatar întâi"))
        _send_email(cfg, text)


def link(cam_id, ts=None):
    """Legatura direct in redare (#pb/<cam>/<zi>/<secunda>, cu 10 s inainte) — doar daca
    Setari are adresa interfetei (web_url); altfel sir gol. De pus la coada textului."""
    base = (settings.get("web_url") or "").strip().rstrip("/")
    if not base or cam_id is None:
        return ""
    lt = time.localtime(ts or time.time())
    sec = max(0, lt.tm_hour * 3600 + lt.tm_min * 60 + lt.tm_sec - 10)
    return f"\n🔗 {base}/#pb/{cam_id}/{time.strftime('%Y-%m-%d', lt)}/{sec}"


def detection(cam_id, label, jpeg=None, poza=None):
    """Notificare pentru o detectie YOLO (doar etichetele alese in Setari). `poza` = (zi, fisier)
    snapshotului salvat de snaps.save_frame: cu el, mesajul Telegram are butoanele ✓ ✕ ∅
    (detectie_feedback), iar verdictul omului se leaga de chenarul din sidecar."""
    if label not in settings.get("notify_labels"):
        return
    name = _cam_name(cam_id)
    taste = None
    try:
        import detectie_feedback
        ref = detectie_feedback.din_poza(cam_id, poza)
        if ref:
            taste = detectie_feedback.taste(cam_id, *ref)
    except Exception as e:
        logare.info(f"[notify] butoane detectie: {str(e)[:60]}")
    send(t("🚨 {cam}: {ce} detectat ({ora})", cam=name, ce=t(RO.get(label, label)),
           ora=time.strftime('%H:%M:%S'))
         + (("\n" + t("E corect?")) if taste else "") + link(cam_id), jpeg=jpeg, key=(cam_id, label), taste=taste)


def face(cam_id, person, jpeg=None, ref=None):
    """Notificare pentru o persoana recunoscuta (Face ID) — cu numele ei. `ref` = (zi, HHMMSS)
    al crop-ului salvat de faces.save_recognized: cu el, mesajul are butoanele ✓/✕ (fata_feedback),
    iar verdictul „nu e el" muta crop-ul live in unknown/ pentru re-etichetare."""
    if not settings.get("face_notify"):
        return
    taste = None
    try:
        import fata_feedback
        if ref:
            taste = fata_feedback.taste(cam_id, ref[0], ref[1], person)
    except Exception as e:
        logare.info(f"[notify] butoane fata: {str(e)[:60]}")
    send(f"👤 {_cam_name(cam_id)}: {person} ({time.strftime('%H:%M:%S')})"
         + (("\n" + t("E corect?")) if taste else "") + link(cam_id),
         jpeg=jpeg, key=(cam_id, "fata-" + person), taste=taste)


def plate(cam_id, numar, nume=None, jpeg=None, crop=None):
    """Notificare pentru un numar de inmatriculare citit (LPR). `crop` = calea crop-ului salvat
    de plates (crops/<zi>/<HHMMSS>_<placa>.jpg): cu ea, mesajul are butoanele ✓/✕ (numar_feedback),
    iar verdictul „greșit" scoate citirea din jurnal."""
    taste = None
    try:
        import numar_feedback
        ref = numar_feedback.din_crop(cam_id, crop, numar)
        if ref:
            taste = numar_feedback.taste(cam_id, ref[0], ref[1], numar)
    except Exception as e:
        logare.info(f"[notify] butoane numar: {str(e)[:60]}")
    if nume:
        if not settings.get("lpr_notify_known"):
            return
        text = f"🚗 {_cam_name(cam_id)}: {nume} ({numar})"
    else:
        cams = [int(c) for c in (settings.get("lpr_alert_cams") or [])]
        straina = (settings.get("lpr_notify_unknown")
                   and (not cams or int(cam_id) in cams))
        if straina:
            text = t("🚗⚠️ {cam}: mașină necunoscută, număr {numar}", cam=_cam_name(cam_id), numar=numar)
        elif settings.get("lpr_notify_all"):
            text = t("🚗 {cam}: număr {numar}", cam=_cam_name(cam_id), numar=numar)
        else:
            return
    send(f"{text} ({time.strftime('%H:%M:%S')})"
         + (("\n" + t("Numărul e corect?")) if taste else "") + link(cam_id),
         jpeg=jpeg, key=(cam_id, "lpr-" + numar), taste=taste)


def _cam_name(cam_id):
    try:
        with open(EXT_FILE) as f:
            for c in json.load(f):
                if c["id"] == cam_id:
                    return c.get("name") or f"cam {cam_id}"
    except (FileNotFoundError, ValueError):
        pass
    return f"cam {cam_id}"


# ---- watchdog: camere standalone + NVR (offline/online) + spatiu pe disc ----
_states = {}  # cam_id / "nvr" -> True (online) / False
_disk_alerted = 0
_disk_vazut = False


def _inchide_episod(tip, episod, **context):
    """La prima verificare dupa pornire, cu starea buna: inchide in serviciul de alerte un episod
    ramas deschis de dinainte de repornire (altfel urmatoarea cadere ar fi doar „repeat”, fara
    mesaj). Nimic deschis = no-op la serviciu; fara rezerva pe bot."""
    import escaladare
    escaladare.alerta(tip, t("✅ Revenit (verificat după repornirea serverului)"), episod=episod,
                      rezolvat=True, rezerva=False, **context)


def offline_ids():
    return [cid for cid, ok in _states.items() if not ok]


def _check_disk():
    """Alerta cand spatiul liber se apropie de pragul de stergere (max 1 mesaj / 12h)."""
    global _disk_alerted, _disk_vazut
    import shutil as sh
    import config as C
    import escaladare
    free_gb = sh.disk_usage(os.path.join(ROOT, "data")).free / 2**30
    limit = C.RECORD_MIN_FREE_GB * 2
    if not _disk_vazut:
        _disk_vazut = True
        if free_gb >= limit:
            _inchide_episod("nvr_disc_plin", "disc", liber=f"{free_gb:.0f}")
    if free_gb < limit and time.time() - _disk_alerted > 12 * 3600:
        _disk_alerted = time.time()
        escaladare.alerta("nvr_disc_plin", t("💾 Atenție: doar {liber} GB liberi pe disc (sub {prag} GB încep să se șteargă înregistrările vechi)",
                          liber=f"{free_gb:.0f}", prag=C.RECORD_MIN_FREE_GB),
                          episod="disc", revenire="nvr_disc_ok", liber=f"{free_gb:.0f}")
    elif _disk_alerted and free_gb >= limit * 1.1:    # histerezis: nu oscila la prag
        _disk_alerted = 0
        escaladare.alerta("nvr_disc_plin", t("💾 Spațiu pe disc din nou suficient: {liber} GB liberi",
                          liber=f"{free_gb:.0f}"), episod="disc", rezolvat=True, liber=f"{free_gb:.0f}")


def _check_nvr():
    import config as C
    if settings.nvr_retras():          # scos din functiune: nu-l mai supraveghem
        _states.pop("nvr", None)
        _fails.pop("nvr", None)
        return
    ok = False
    try:
        s = requests.Session()
        s.auth = HTTPDigestAuth(C.NVR_USER, C.NVR_PASS)
        ok = s.get(f"http://{C.NVR_IP}/ISAPI/System/deviceInfo", timeout=6).ok
    except requests.RequestException:
        pass
    _fails["nvr"] = 0 if ok else _fails.get("nvr", 0) + 1
    prev = _states.get("nvr")
    if prev is None:
        # la pornire: online la primul succes; daca NVR-ul e deja cazut, spunem o data —
        # altfel un NVR oprit inainte de repornirea serverului n-ar fi semnalat niciodata
        if ok:
            _states["nvr"] = True
            _inchide_episod("nvr_nvr_cazut", "nvr", ip=C.NVR_IP)
        elif _fails["nvr"] >= OFFLINE_DUPA:
            _states["nvr"] = False
            _alerta_nvr(False, t("⚠️ NVR OFFLINE (de la pornirea serverului): {ip}", ip=C.NVR_IP))
        return
    nou = _stare_noua(prev, ok, _fails["nvr"])   # acelasi debounce ca la camere
    if nou != prev:
        _states["nvr"] = nou
        _alerta_nvr(nou, t("✅ NVR revenit: {ip}", ip=C.NVR_IP) if nou else t("⚠️ NVR OFFLINE: {ip}", ip=C.NVR_IP))


def _alerta_nvr(ok, text):
    import config as C
    import escaladare
    escaladare.alerta("nvr_nvr_cazut", text, episod="nvr", rezolvat=ok, revenire="nvr_nvr_revenit",
                      ip=C.NVR_IP)


def stare_nvr():
    """True/False dupa ultima verificare a watchdog-ului, None cat inca nu stim."""
    return _states.get("nvr")


def stare_cam(cid):
    """Starea unei camere dupa watchdog: True (online) / False (offline) / None (inca necunoscut).
    `_states` e tinut de watchdog-ul care ruleaza in procesul web (vezi start_watchdog)."""
    try:
        return _states.get(int(cid))
    except (ValueError, TypeError):
        return None


_stream_alerted = 0


def _check_streams():
    """Fiecare camera trebuie sa aiba un stream in motorul de streaming. Daca lipseste,
    camera nu se vede in interfata desi e sanatoasa — semnalam (max 1 mesaj / 6h)."""
    global _stream_alerted
    import config as C
    try:
        with open(EXT_FILE) as f:
            ids = [c["id"] for c in json.load(f)]
        have = requests.get(f"{C.GO2RTC_API}/api/streams", timeout=8).json()
    except (requests.RequestException, ValueError, FileNotFoundError):
        return
    lipsa = [i for i in ids if f"cam{i}" not in have]
    if lipsa and time.time() - _stream_alerted > 6 * 3600:
        _stream_alerted = time.time()
        import escaladare
        escaladare.alerta("nvr_fluxuri_lipsa",
                          t("⚠️ Camerele {lista} nu au flux video configurat — nu se văd în interfață", lista=lipsa))
        logare.info(f"[notify] camere fara stream: {lipsa}")


# Debounce: offline abia dupa atatea maturi esuate LA RAND (~2 min). Un singur
# timeout ISAPI e adesea fals: NVR-ul isi plimba periodic config-push-ul prin
# camerele proxied (canal 1->2->3...) si le tine serverul web ocupat cateva zeci
# de secunde — cu RTSP-ul curgand netulburat. Dovedit 2026-09-09: "offline"
# cam1-2-3 la 13:03 si 21:23, ambele de ~70s (o singura matura), cu ZERO goluri
# de inregistrare. Revenirea ramane instant (primul succes).
OFFLINE_DUPA = 2
_fails = {}


def _stare_noua(prev, ok, esecuri):
    """Pur si testabil: starea declarata dupa o verificare. Online la primul
    succes; offline abia dupa OFFLINE_DUPA esecuri consecutive."""
    if ok:
        return True
    return False if esecuri >= OFFLINE_DUPA else prev


# ---- camera cazuta: prima alerta, apoi mementouri exponentiale, apoi zilnic, pana revine ----
# Starea sta pe disc (data/camere_cazute.json: {cid: {de_la, n}}), ca programul sa continue peste
# o repornire si sa stim la pornire ca o camera era deja cazuta (sau a revenit intre timp).
CAZUTE_FILE = os.path.join(ROOT, "data", "camere_cazute.json")
# dupa a n-a alerta (n >= 1), urmatoarea vine cand camera e cazuta de MEMENTO[n-1] secunde;
# dupa ultima treapta (1 zi), cate una pe zi — o camera moarta nu are voie sa fie uitata
MEMENTO = (240, 480, 960, 1920, 3600, 7200, 14400, 28800, 57600, 86400)


def memento_scadent(de_la, n, acum):
    """Pur si testabil: dupa n alerte trimise, e vremea urmatoarei?"""
    i = max(0, n - 1)
    prag = MEMENTO[i] if i < len(MEMENTO) else 86400 * (i - len(MEMENTO) + 2)
    return acum - de_la >= prag


def durata(sec):
    """„7 min”, „3 h 10 min”, „13 zile 4 h” — cat a stat camera cazuta."""
    m = int(sec // 60)
    if m < 60:
        return t("{n} min", n=max(1, m))
    h, m = divmod(m, 60)
    if h < 24:
        return t("{h} h {m} min", h=h, m=m) if m else t("{h} h", h=h)
    z, h = divmod(h, 24)
    return t("{z} zile {h} h", z=z, h=h) if z > 1 else t("1 zi {h} h", h=h)


def _cazute():
    import store
    return store.load_json(CAZUTE_FILE, None) or {}


def _scrie_cazute(d):
    import store
    try:
        store.atomic_write_json(CAZUTE_FILE, d)
    except OSError as e:
        logare.info(f"[notify] camere cazute: {e}")


def _de_cand_cazuta(cid):
    """Pentru o camera gasita cazuta la pornire, fara stare salvata: ultimul camera_offline din
    jurnal, daca nu e urmat de un camera_online. Altfel None (cazuta „de acum”)."""
    try:
        import jurnal
        jos = jurnal.query(limit=1, tip="camera_offline", cam=cid)
        sus = jurnal.query(limit=1, tip="camera_online", cam=cid)
        if jos and (not sus or sus[0]["t"] < jos[0]["t"]):
            return time.mktime(time.strptime(jos[0]["t"], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        pass
    return None


def _cam_cazuta(cam, acum, la_pornire=False):
    import escaladare
    cid, nume = cam["id"], cam.get("name") or f"cam {cam['id']}"
    de_la = _de_cand_cazuta(cid) if la_pornire else None
    if de_la is None:
        de_la = acum
    n = 1
    while memento_scadent(de_la, n, acum):     # cazuta de mult: sarim treptele trecute
        n += 1
    d = _cazute()
    d[str(cid)] = {"de_la": de_la, "n": n}
    _scrie_cazute(d)
    if acum - de_la >= 120:
        text = t("⚠️ Camera OFFLINE de {durata}: {cam} ({ip})", durata=durata(acum - de_la), cam=nume, ip=cam.get("ip"))
    elif la_pornire:
        text = t("⚠️ Camera OFFLINE (de la pornirea serverului): {cam} ({ip})", cam=nume, ip=cam.get("ip"))
    else:
        text = t("⚠️ Camera OFFLINE: {cam} ({ip})", cam=nume, ip=cam.get("ip"))
    escaladare.alerta("nvr_camera_cazuta", text, episod=f"cam-{cid}", revenire="nvr_camera_revenita",
                      cam=nume, ip=cam.get("ip"))


def _cam_memento(cam, acum):
    """Camera e tot cazuta: memento dupa program (fara episod — episodul deschis ar inghiti
    mesajul ca „repeat”; acelasi sablon, deci aceleasi canale)."""
    import escaladare
    d = _cazute()
    e = d.get(str(cam["id"]))
    if not e:
        return _cam_cazuta(cam, acum)
    if not memento_scadent(e["de_la"], e["n"], acum):
        return
    e["n"] += 1
    _scrie_cazute(d)
    nume = cam.get("name") or f"cam {cam['id']}"
    escaladare.alerta("nvr_camera_cazuta", t("⏰ Camera {cam} ({ip}) e tot OFFLINE, de {durata}",
                                             cam=nume, ip=cam.get("ip"), durata=durata(acum - e["de_la"])),
                      cam=nume, ip=cam.get("ip"))


def _cam_revenita(cam, acum):
    import escaladare
    d = _cazute()
    e = d.pop(str(cam["id"]), None)
    _scrie_cazute(d)
    nume = cam.get("name") or f"cam {cam['id']}"
    dupa = t(" după {durata}", durata=durata(acum - e["de_la"])) if e else ""
    escaladare.alerta("nvr_camera_cazuta", t("✅ Camera revenita: {cam} ({ip})", cam=nume, ip=cam.get("ip")) + dupa,
                      episod=f"cam-{cam['id']}", rezolvat=True, cam=nume, ip=cam.get("ip"))


def _schimbare_cam(cid, nou):
    """Jurnal + incident pentru o camera cazuta/revenita (reguli, webhook, triaj)."""
    try:
        import hooks
        hooks.emit("camera_online" if nou else "camera_offline", cid)
    except Exception:
        pass
    if nou:
        # camera a revenit -> incidentul de offline nu mai e relevant
        try:
            import incidents
            incidents.resolve_recent(cid, "camera_offline")
        except Exception:
            pass


def _verifica_cam(cam, ok, acum):
    """O verificare a unei camere: starea (cu debounce) + alertele. Separat de bucla, testabil."""
    cid = cam["id"]
    _fails[cid] = 0 if ok else _fails.get(cid, 0) + 1
    prev = _states.get(cid)
    if prev is None:
        # prima verificare de la pornire: comparam cu ce stiam inainte de repornire
        salvata = str(cid) in _cazute()
        if ok:
            _states[cid] = True
            if salvata:
                _cam_revenita(cam, acum)          # a revenit cat serverul era oprit
                _schimbare_cam(cid, True)
            else:
                _inchide_episod("nvr_camera_cazuta", f"cam-{cid}",
                                cam=cam.get("name") or f"cam {cid}", ip=cam.get("ip"))
        elif _fails[cid] >= OFFLINE_DUPA:
            _states[cid] = False
            if salvata:
                _cam_memento(cam, acum)           # programul continua de unde a ramas
            else:
                _cam_cazuta(cam, acum, la_pornire=True)
                _schimbare_cam(cid, False)
        return
    nou = _stare_noua(prev, ok, _fails[cid])
    if nou != prev:
        _states[cid] = nou
        (_cam_revenita if nou else _cam_cazuta)(cam, acum)
        _schimbare_cam(cid, nou)
    elif not nou:
        _cam_memento(cam, acum)


def _watchdog():
    while True:
        try:
            _check_nvr()
            _check_disk()
            _check_streams()
        except Exception as e:
            # watchdog-ul care moare tacut = exact defectiunea pe care o pazeste
            logare.info(f"[notify] watchdog: {str(e)[:80]}")
        try:
            with open(EXT_FILE) as f:
                cams = json.load(f)
        except (FileNotFoundError, ValueError):
            cams = []
        # + camerele de pe NVR accesate direct (watchdog pe fiecare)
        try:
            with open(os.path.join(ROOT, "data", "nvrcams.json")) as f:
                cams += [{"id": int(ch), "name": t("canal {ch} ({model})", ch=ch, model=e.get('model', '?')), **e}
                         for ch, e in json.load(f).items()]
        except (FileNotFoundError, ValueError):
            pass
        import drivers
        for cam in cams:
            try:
                # ISAPI deviceInfo sau ONVIF GetDeviceInformation
                _verifica_cam(cam, drivers.ping(cam), time.time())
            except Exception as e:
                logare.info(f"[notify] watchdog cam{cam.get('id')}: {str(e)[:80]}")
        ids = {c["id"] for c in cams}
        for cid in list(_states):
            if cid != "nvr" and cid not in ids:
                del _states[cid]
                _fails.pop(cid, None)
        d = _cazute()
        if any(int(k) not in ids for k in d):    # camera stearsa din configurare: uitam de ea
            _scrie_cazute({k: v for k, v in d.items() if int(k) in ids})
        time.sleep(60)


def start_watchdog():
    threading.Thread(target=_watchdog, daemon=True, name="cam-watchdog").start()
