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
      "chicken": "Gaina", "pigeon": "Porumbel", "car": "Masina",
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


def _send_telegram(cfg, text, jpeg=None):
    base = f"https://api.telegram.org/bot{cfg['telegram_token']}"
    if jpeg:
        r = requests.post(f"{base}/sendPhoto",
                          data={"chat_id": cfg["telegram_chat"], "caption": text},
                          files={"photo": ("s.jpg", jpeg, "image/jpeg")}, timeout=20)
    else:
        r = requests.post(f"{base}/sendMessage",
                          data={"chat_id": cfg["telegram_chat"], "text": text}, timeout=20)
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


def send(text, jpeg=None, key=None, sync=False):
    """Trimite pe toate canalele configurate. key = throttle per subiect.
    sync=True -> ruleaza in fir si ridica exceptii (pentru butoanele de test)."""
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
            _send_telegram(cfg, text, jpeg)
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


def detection(cam_id, label, jpeg=None):
    """Notificare pentru o detectie YOLO (doar etichetele alese in Setari)."""
    if label not in settings.get("notify_labels"):
        return
    name = _cam_name(cam_id)
    send(t("🚨 {cam}: {ce} detectat ({ora})", cam=name, ce=t(RO.get(label, label)),
           ora=time.strftime('%H:%M:%S'))
         + link(cam_id), jpeg=jpeg, key=(cam_id, label))


def face(cam_id, person, jpeg=None):
    """Notificare pentru o persoana recunoscuta (Face ID) — cu numele ei."""
    if not settings.get("face_notify"):
        return
    send(f"👤 {_cam_name(cam_id)}: {person} ({time.strftime('%H:%M:%S')})" + link(cam_id),
         jpeg=jpeg, key=(cam_id, "fata-" + person))


def plate(cam_id, numar, nume=None, jpeg=None):
    """Notificare pentru un numar de inmatriculare citit (LPR)."""
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
    send(f"{text} ({time.strftime('%H:%M:%S')})" + link(cam_id), jpeg=jpeg,
         key=(cam_id, "lpr-" + numar))


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


def offline_ids():
    return [cid for cid, ok in _states.items() if not ok]


def _check_disk():
    """Alerta cand spatiul liber se apropie de pragul de stergere (max 1 mesaj / 12h)."""
    global _disk_alerted
    import shutil as sh
    import config as C
    free_gb = sh.disk_usage(os.path.join(ROOT, "data")).free / 2**30
    limit = C.RECORD_MIN_FREE_GB * 2
    if free_gb < limit and time.time() - _disk_alerted > 12 * 3600:
        _disk_alerted = time.time()
        send(t("💾 Atentie: doar {liber}GB liberi pe disc (sub {prag}GB incep sa se stearga inregistrarile vechi)",
               liber=f"{free_gb:.0f}", prag=C.RECORD_MIN_FREE_GB))


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
        elif _fails["nvr"] >= OFFLINE_DUPA:
            _states["nvr"] = False
            send(t("⚠️ NVR OFFLINE (de la pornirea serverului): {ip}", ip=C.NVR_IP))
        return
    nou = _stare_noua(prev, ok, _fails["nvr"])   # acelasi debounce ca la camere
    if nou != prev:
        _states["nvr"] = nou
        send(t("✅ NVR revenit: {ip}", ip=C.NVR_IP) if nou else t("⚠️ NVR OFFLINE: {ip}", ip=C.NVR_IP))


def stare_nvr():
    """True/False dupa ultima verificare a watchdog-ului, None cat inca nu stim."""
    return _states.get("nvr")


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
        send(t("⚠️ Camerele {lista} nu au flux video configurat — nu se vad in interfata", lista=lipsa))
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
        for cam in cams:
            import drivers
            ok = drivers.ping(cam)          # ISAPI deviceInfo sau ONVIF GetDeviceInformation
            cid = cam["id"]
            _fails[cid] = 0 if ok else _fails.get(cid, 0) + 1
            prev = _states.get(cid)
            if prev is None:
                _states[cid] = ok
                continue
            nou = _stare_noua(prev, ok, _fails[cid])
            if nou != prev:
                _states[cid] = nou
                nume = cam.get("name") or f"cam {cid}"
                send(t("✅ Camera revenita: {cam} ({ip})", cam=nume, ip=cam['ip']) if nou
                     else t("⚠️ Camera OFFLINE: {cam} ({ip})", cam=nume, ip=cam['ip']))
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
        ids = {c["id"] for c in cams}
        for cid in list(_states):
            if cid != "nvr" and cid not in ids:
                del _states[cid]
                _fails.pop(cid, None)
        time.sleep(60)


def start_watchdog():
    threading.Thread(target=_watchdog, daemon=True, name="cam-watchdog").start()
