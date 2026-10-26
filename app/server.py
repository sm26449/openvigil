#!/usr/bin/env python3
"""Backend interfata NVR Hikvision.
- /api/cameras            -> lista camerelor (din ISAPI, cache)
- /api/recordings         -> cauta inregistrarile (ISAPI ContentMgmt/search)
- /api/play               -> inregistreaza un stream de playback/HD in go2rtc, intoarce numele
Streaming-ul efectiv (MSE/WebRTC) e servit de go2rtc; frontend-ul vorbeste direct cu el.
"""
import hashlib, hmac, ipaddress, json, os, re, secrets, subprocess, sys, threading, time, urllib.parse
from flask import (Flask, Response, g, redirect, request, jsonify,
                   send_from_directory, session)
from flask.sessions import SecureCookieSessionInterface
from werkzeug.middleware.proxy_fix import ProxyFix
import requests
from requests.auth import HTTPDigestAuth

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.makedirs(os.path.join(ROOT, "data"), exist_ok=True)   # prima pornire, instalare clasica
import config as C
import gen_go2rtc
import audio_level
import backup_nocturn
import mqtt as mqtt_bridge
import continuity
import hooks
import audit
import i18n
from i18n import t
import jobs
import jurnal
import masca
import mod
import scoping
import sdnotify
import camctl
import describe
import detect
import drivers
import faces
import notify
import plates
import push
import record
import reencode
import semantic
import settings
import snaps
import store
import stream_proxy
import telegram_bot
import track
import users
import logare

NS = {"h": "http://www.hikvision.com/ver20/XMLSchema"}
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
EXT_FILE = os.path.join(ROOT, "data", "cameras.json")

app = Flask(__name__, static_folder=None)


# ---- in spatele unui proxy HTTPS (Traefik/Caddy) ----
# Antetele X-Forwarded-For/Proto/Host sunt crezute DOAR cand cererea vine de la o adresa de
# incredere (implicit: localhost si retelele Docker, de unde vine Traefik-ul care termina
# TLS-ul). Un client din LAN care bate direct in :8079 nu poate astfel sa-si falsifice IP-ul
# (rate-limit la login, audit) si nici sa para „https" ca sa primeasca cookie Secure.
_PROXY_NETS = [ipaddress.ip_network(n) for n in
               getattr(C, "TRUSTED_PROXIES", ("127.0.0.0/8", "::1/128"))]


class _DinSpateleProxyului:
    def __init__(self, wsgi):
        self.direct = wsgi
        self.prin_proxy = ProxyFix(wsgi, x_for=1, x_proto=1, x_host=1, x_port=1)

    def __call__(self, environ, start_response):
        try:
            peer = ipaddress.ip_address(environ.get("REMOTE_ADDR", ""))
            de_incredere = any(peer in n for n in _PROXY_NETS)
        except ValueError:
            de_incredere = False
        return (self.prin_proxy if de_incredere else self.direct)(environ, start_response)


app.wsgi_app = _DinSpateleProxyului(app.wsgi_app)
nvr = requests.Session()
nvr.auth = HTTPDigestAuth(C.NVR_USER, C.NVR_PASS)


# ---- login: conturi cu roluri (admin / viewer), obligatoriu ----
def _secret_key():
    p = os.path.join(ROOT, "data", "secret_key")
    try:
        with open(p, "rb") as f:
            return f.read()
    except FileNotFoundError:
        key = os.urandom(32)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key


app.secret_key = _secret_key()
app.permanent_session_lifetime = 30 * 24 * 3600  # 30 de zile de la ultima folosire
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3 + 16 * 1024 ** 2   # /verifica: MP4 pana la 2 GB
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                  SESSION_COOKIE_SECURE=bool(getattr(C, "HTTPS", False)),
                  # optional (COOKIE_DOMAIN=".exemplu.ro" in .env) ca sesiunea sa fie valabila
                  # si pe alte subdomenii puse in spatele login-ului NVR prin /api/auth/forward
                  SESSION_COOKIE_DOMAIN=getattr(C, "COOKIE_DOMAIN", None) or None,
                  # cookie-ul se (re)trimite DOAR cand sesiunea se schimba (login, 2FA, logout):
                  # altfel Flask il retrimitea la orice raspuns, iar un raspuns intarziat la o
                  # cerere pornita inainte de /logout reinstala sesiunea veche dupa deconectare.
                  # Pretul: expirarea de 30 de zile curge de la login, nu glisant.
                  SESSION_REFRESH_EACH_REQUEST=False)


class _CookieSecurePeHttps(SecureCookieSessionInterface):
    """Cookie-ul de sesiune primeste `Secure` cand cererea a venit pe HTTPS (prin proxy),
    si ramane simplu pe HTTP-ul din LAN: aceeasi instalare merge pe ambele cai, iar
    cookie-ul dat pe https nu pleaca niciodata in clar. HTTPS=True in config il forteaza."""
    def get_cookie_secure(self, app):
        return bool(app.config["SESSION_COOKIE_SECURE"] or request.is_secure)


app.session_interface = _CookieSecurePeHttps()

# migrare: vechea parola unica din Setari devine contul "admin" (acelasi hash)
if not users.load() and settings.get("web_password"):
    users.save([{"user": "admin", "hash": settings.get("web_password"), "role": "admin"}])
    logare.info("[server] parola veche din Setari migrata in contul 'admin'")


def current_user():
    """Contul sesiunii — sau None daca sesiunea a fost revocata (versiunea din cookie
    difera de `sess_ver` a contului: parola schimbata, „deconecteaza toate dispozitivele")."""
    name = session.get("user")
    if not name:
        return None
    u = users.get(name)
    if not u or int(session.get("v") or 0) != users.sess_ver(u):
        return None
    return u


# GET-uri cu efect (creeaza joburi, pornesc fluxuri): cookie-ul Lax nu pleaca pe subresurse
# cross-site, dar o navigare de pe alt site l-ar purta; browserul spune de unde vine cererea
GET_CU_EFECT = ("/api/clip", "/api/export", "/api/timelapse", "/api/chenare", "/api/play", "/api/regiune", "/api/heatmap")
# Ce are voie sa vada un viewer/operator (GET). Restul de /api/* e doar admin.
VIEWER_GET = ("/api/me", "/api/cameras", "/api/events", "/api/pulse", "/api/recordings",
              "/api/play", "/api/clip", "/api/export", "/api/alarms", "/api/thumb/",
              "/api/noise", "/api/views", "/api/storage", "/api/search", "/api/ask",
              "/api/incidents", "/api/cases", "/api/digest", "/api/speedtest", "/snapshots/",
              "/api/marcaje", "/api/jobs", "/api/push", "/api/mod", "/api/harta",
              "/api/regiune", "/api/heatmap", "/api/timelapse", "/api/segmente",
              "/api/flux", "/api/journal", "/api/plates", "/api/faces/history", "/api/reid/")
# Actiunile live permise OPERATORULUI (paznicului): POST pe camera lui, fara nimic
# de configurare (config/stream/zone/reboot/detect raman doar pentru admin).
OPERATOR_ACTIONS = ("siren", "strobe", "output", "say", "volume", "ptz", "presets",
                    "focus", "health")


def _visible_ids():
    """None = toate camerele; multime = doar acestea (viewer restrans)."""
    u = current_user()
    if not u or u.get("role") == "admin":
        return None
    return users.allowed_ids(u, get_cameras())


@app.errorhandler(jobs.PreaMulte)
def _prea_multe_joburi(e):
    return jsonify({"error": t("ai deja prea multe joburi în lucru; așteaptă să se termine")}), 429


@app.before_request
def guard():
    p = request.path
    if p.startswith("/api/v1/"):
        return None          # API-ul de automatizari are jetonul lui (X-Token)
    if p.startswith("/api/internal/"):
        return None          # canalul nvr-ai (localhost + token, verificat in endpoint)
    if p in ("/login", "/login/2fa", "/setup", "/logout", "/healthz", "/api/auth/forward", "/verifica"):
        return None          # forward-ul isi verifica singur sesiunea (401 fara redirect)
    if request.method == "GET" and request.headers.get("Sec-Fetch-Site") == "cross-site" \
            and any(p == x or p.startswith(x + "/") for x in GET_CU_EFECT):
        return jsonify({"error": "cerere din alt site"}), 403
    if not users.load():     # prima pornire: totul duce la crearea contului admin
        if p.startswith("/api/"):
            return jsonify({"error": t("creează întâi contul de administrator")}), 401
        return redirect("/setup")
    u = current_user()
    if not u:
        # cookie-ul vechi/revocat se arunca — dar nu si pre-biletul 2FA: utilizatorul e
        # pe pagina codului, iar un alt tab (sau service worker-ul) care cere /api/* intre
        # timp nu are voie sa-i stearga pasul doi de sub picioare
        if "pre2fa" not in session:
            session.clear()
        if p.startswith("/api/"):
            return jsonify({"error": t("neautentificat")}), 401
        return redirect("/login")
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        # CSRF: interfata trimite jetonul sesiunii in antetul X-CSRF la orice modificare
        if not hmac.compare_digest(request.headers.get("X-CSRF", ""),
                                   session.get("csrf", "")):
            return jsonify({"error": t("sesiune expirata — reîncarcă pagina")}), 403
        if u["role"] == "operator" and request.method == "POST":
            # paznicul actioneaza (sirena, vorbit, PTZ...) pe camerele lui, atat
            m = re.match(r"^/api/cam/(\d+)/([a-z-]+)$", p)
            if m and m[2] in OPERATOR_ACTIONS:
                vis = _visible_ids()
                if vis is None or int(m[1]) in vis:
                    return None
                return jsonify({"error": t("nu ai acces la această camera")}), 403
            # triajul incidentelor e treaba paznicului: confirma / marcheaza fals
            if re.match(r"^/api/incidents/[a-z0-9]+/stare$", p):
                return None
        # dosarele de caz le construieste paznicul: creare/adaugare/stergere proba
        if u["role"] == "operator" and re.match(
                r"^/api/cases(/[a-z0-9]+(/proba(/[a-z0-9]+)?)?)?$", p):
            return None
        # marcajele pe inregistrari: la fel (ruta verifica singura camera)
        if u["role"] == "operator" and re.match(r"^/api/marcaje(/[a-z0-9]+)?$", p):
            return None
        # isi poate opri joburile lui (ruta verifica proprietarul)
        if u["role"] == "operator" and request.method == "DELETE" \
                and re.match(r"^/api/jobs/[a-z0-9]+$", p):
            return None
        # contul propriu (2FA, deconectarea dispozitivelor) si notificarile push pe
        # telefonul lui: orice rol, inclusiv viewer — nu modifica nimic din sistem
        if re.match(r"^/api/(me/|push/)", p):
            return None
        # modul acasa/plecat/noapte e „armarea" — treaba paznicului, nu configurare
        if u["role"] == "operator" and p == "/api/mod":
            return None
        if u["role"] != "admin":
            return jsonify({"error": t("doar administratorul poate face modificări")}), 403
    elif u["role"] != "admin" and p.startswith("/api/"):
        if not any(p == w or p.startswith(w) for w in VIEWER_GET):
            return jsonify({"error": t("nu ai acces la această sectiune")}), 403
    # restrictia pe camere, pentru ORICE cerere a unui cont restrans (GET sau modificare):
    # id in cale, `channel`/`cam`/`camera` in query sau in corpul JSON (scoping.py)
    if u["role"] != "admin" and p.startswith("/api/"):
        vis = _visible_ids()
        if vis is not None and not scoping.permisa(p, request.args, request.get_json(silent=True), vis):
            return jsonify({"error": t("nu ai acces la această camera")}), 403
    return None


# limba interfetei: preferinta contului (Contul meu) cand exista o sesiune
i18n.pref_utilizator = lambda: (current_user() or {}).get("lang")


@app.after_request
def _cookie_limba(resp):
    """?lang=en (sau alegerea din Contul meu) ramane in cookie-ul `lang`, un an."""
    limba = getattr(g, "limba_cookie", None)
    if limba == "" and "lang" in request.cookies:
        resp.delete_cookie("lang", domain=app.config.get("SESSION_COOKIE_DOMAIN") or None)
    elif limba and request.cookies.get("lang") != limba:
        resp.set_cookie("lang", limba, max_age=365 * 86400, httponly=True, samesite="Lax",
                        secure=app.config["SESSION_COOKIE_SECURE"],
                        domain=app.config.get("SESSION_COOKIE_DOMAIN") or None)
    return resp


@app.after_request
def security_headers(resp):
    """CSP: scripturi doar ale noastre (app.js e modul, fara handlere inline), fluxurile
    video doar prin releul :8076 al acestui host, nimic altceva de pe alte origini.
    Stilurile inline raman permise (atributele style= din index.html si din app.js).
    Impreuna cu scaparea din html`` (app.js), un nume de camera sau un text de eveniment
    cu <script> nu mai are cum sa ruleze — si nici n-ar avea de unde sa-si incarce codul."""
    host = urllib.parse.urlsplit("//" + request.host).hostname or "localhost"
    if ":" in host:                      # IPv6 literal
        host = f"[{host}]"
    releu = f"{host}:{getattr(C, 'STREAM_PORT', 8076)}"
    # pe HTTPS (prin Traefik) fluxurile vin pe aceeasi origine (wss://host/api/ws, rutat
    # de proxy catre releu), nu pe :8076 — de aceea si wss://host, nu doar wss://host:8076
    resp.headers.setdefault("Content-Security-Policy", "; ".join([
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        f"img-src 'self' data: blob: http://{releu} https://{releu}",
        f"media-src 'self' blob: http://{releu} https://{releu}",
        f"connect-src 'self' ws://{releu} wss://{releu} http://{releu} https://{releu} "
        f"wss://{host} ws://{host}",
        "object-src 'none'", "base-uri 'self'", "form-action 'self'",
        "frame-ancestors 'self'",
    ]))
    if request.is_secure:
        # odata vazut pe HTTPS, browserul nu mai incearca HTTP pe acest nume (un an)
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.after_request
def _audit_modificari(resp):
    """Plasa de siguranta: orice modificare reusita neauditata explicit ajunge in jurnal."""
    audit.cerere_neauditata(resp)
    return resp


def detect_hwaccel():
    """Returneaza True daca NVENC (GPU NVIDIA) functioneaza la runtime."""
    try:
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-f", "lavfi",
             "-i", "testsrc=size=320x240:rate=10", "-frames:v", "5",
             "-c:v", "h264_nvenc", "-f", "null", "-"],
            capture_output=True, timeout=25, check=True)
        return True
    except Exception:
        return False


HW = detect_hwaccel()
logare.info(f"[server] hwaccel GPU (h264_nvenc): {'DA' if HW else 'NU (CPU/libx264)'}")


def isapi(path, method="GET", data=None, cam=None):
    """Apel ISAPI catre NVR (implicit) sau catre o camera standalone (cam = resolve_camera)."""
    if cam and cam["source"] == "ext":
        url = f"http://{cam['ip']}{path}"
        sess = requests.Session()
        sess.auth = HTTPDigestAuth(cam["user"], cam["pass"])
    else:
        url = f"http://{C.NVR_IP}{path}"
        sess = nvr
    r = sess.request(method, url, data=data, timeout=20,
                     headers={"Content-Type": "application/xml"})
    r.raise_for_status()
    return r.text


def strip_ns(tag):
    return tag.split("}", 1)[-1]


def xml_text(root, tag):
    """Primul text al unui element <tag> (ignorand namespace-ul), sau None."""
    for el in root.iter():
        if strip_ns(el.tag) == tag:
            return el.text
    return None


# ---- camere standalone (adaugate manual, persistate in data/cameras.json) ----
# Contin parole -> fisierul se tine cu chmod 600. ID-urile lor incep de la 101 ca sa nu
# se loveasca de canalele NVR (1..16). Live = substream 102 (camN in go2rtc), HD/playback
# = mainstream 101 transcodat (vezi resolve_camera / api_play).
EXT_BASE_ID = 101


def load_ext():
    try:
        with open(EXT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return []


def save_ext(cams):
    store.atomic_write_json(EXT_FILE, cams, mode=0o600, indent=2)


# Orice read-modify-write pe registrul de camere sta sub lock-ul asta: doua editari
# concurente (config + import, sau doua tab-uri) nu-si mai pierd una alteia campurile.
EXT_LOCK = threading.RLock()
IDS_FILE = os.path.join(ROOT, "data", "camera_ids.json")


def next_cam_id(cams):
    """Id-uri MONOTONE: o camera noua nu mosteneste niciodata id-ul (deci inregistrarile,
    istoricul, dosarele) uneia sterse — contorul tine minte cel mai mare id dat vreodata."""
    cel_mai_mare = max([c["id"] for c in cams] + [int((store.load_json(IDS_FILE, {}) or {}).get("ultimul") or 0)],
                       default=EXT_BASE_ID - 1)
    nou = max(cel_mai_mare + 1, EXT_BASE_ID)
    store.atomic_write_json(IDS_FILE, {"ultimul": nou}, mode=0o600)
    return nou


# ---- camere (cache: lista unificata NVR + standalone) ----
_cams = None


def invalidate_cam_cache():
    """get_cameras() re-citeste sursele la urmatorul apel. Blueprint-urile o folosesc
    in locul lui `global _cams` — global-ul lor ar atinge modulul LOR, nu pe acesta."""
    global _cams
    _cams = None


def get_cameras():
    """Lista camerelor — sursa unica de adevar e data/cameras.json (NU mai depinde de NVR).
    Camerele importate de pe NVR pastreaza 'nvr_channel' doar pentru istoricul vechi."""
    global _cams
    if _cams is not None:
        return _cams
    _cams = [{"id": cam["id"], "name": cam.get("name") or f"Camera {cam['id']}",
              "source": "ext", "model": cam.get("model"), "ip": cam["ip"],
              "port": cam.get("port", 554), "user": cam.get("user"),
              "playback": bool(cam.get("playback")),
              "nvr_channel": cam.get("nvr_channel"),
              "record": cam.get("record", "continuous"),
              "group": cam.get("group") or "Altele",
              "retention": cam.get("retention"),
              "mask": masca.rects(cam),
              "vendor": drivers.vendor(cam),
              "features": cam.get("features") or {}}
             for cam in sorted(load_ext(), key=lambda c: c["id"])]
    return _cams


def resolve_camera(cam_id):
    """Datele de conexiune DIRECTA la camera (live, HD, inregistrare) — canale 101/102."""
    for cam in load_ext():
        if cam["id"] == cam_id:
            return {"source": "ext", "ip": cam["ip"], "port": cam.get("port", 554),
                    "user": cam["user"], "pass": cam["pass"],
                    "main": 101, "sub": 102, "vendor": drivers.vendor(cam),
                    "onvif": cam.get("onvif"), "nvr_channel": cam.get("nvr_channel")}
    return {"source": "nvr", "ip": C.NVR_IP, "port": C.NVR_PORT,
            "user": C.NVR_USER, "pass": C.NVR_PASS,
            "main": cam_id * 100 + 1, "sub": cam_id * 100 + 2}


def nvr_history_cam(cam_id):
    """Conexiunea catre NVR pentru istoricul dinainte de migrare (daca a fost importata).
    None cand NVR-ul e retras: nimic nu mai incearca sa-l contacteze."""
    if settings.nvr_retras():
        return None
    for cam in load_ext():
        if cam["id"] == cam_id and cam.get("nvr_channel"):
            ch = cam["nvr_channel"]
            return {"source": "nvr", "ip": C.NVR_IP, "port": C.NVR_PORT,
                    "user": C.NVR_USER, "pass": C.NVR_PASS,
                    "main": ch * 100 + 1, "sub": ch * 100 + 2}
    return None


NVRCAMS_FILE = os.path.join(ROOT, "data", "nvrcams.json")


def sync_all_watchers(ext_cams=None):
    """Urmareste evenimentele de alarma pe toate camerele din registru."""
    camctl.sync_watchers(list(load_ext() if ext_cams is None else ext_cams))


def _cam_entry(cam_id):
    """Camera cu credentiale + features din registru. Toate actiunile (sirena, volum,
    zoom, smart...) merg prin asta — capability-driven, fara liste de modele."""
    return next((c for c in load_ext() if c["id"] == cam_id), None)


def reload_streams():
    """Regenereaza go2rtc.yaml (persista la reboot) si reincarca config-ul in go2rtc-ul pornit.
    Verifica apoi ca fiecare camera chiar are un stream — o generare incompleta ar scoate
    o camera din live fara ca nimeni sa observe (s-a intamplat)."""
    global _cams
    _cams = None
    try:
        inainte = open(gen_go2rtc.OUT).read()
    except OSError:
        inainte = None
    path = gen_go2rtc.generate()
    try:
        written = open(path).read()
        lipsa = [c["id"] for c in load_ext()
                 if f'\n  cam{c["id"]}:' not in written and f'\n  cam{c["id"]}: ' not in written]
        if lipsa:
            logare.warning(f"[server] ATENTIE: camerele {lipsa} lipsesc din configuratia de streaming "
                  f"— regenerez")
            gen_go2rtc.generate()
            notify.send(f"⚠️ Configurația de streaming a fost regenerata: lipseau camerele {lipsa}")
    except OSError as e:
        logare.info(f"[server] verificare configuratie: {e}")
    if written == inainte and _go2rtc_are(f"cam{load_ext()[0]['id']}_main" if load_ext() else None):
        # nimic nou in configuratie si go2rtc o are deja incarcata: nu-l repornim degeaba —
        # o repornire taie toate fluxurile live SI cele 18 recordere (care cad pe sursa directa)
        logare.info("[server] configuratia de streaming neschimbata: go2rtc nu se reporneste")
        return
    logare.info("[server] configuratia de streaming s-a schimbat: repornesc go2rtc")
    try:  # reincarcare la cald; daca API-ul nu suporta, raman streamurile inregistrate dinamic
        requests.post(f"{C.GO2RTC_API}/api/restart", timeout=10)
    except Exception:
        pass


def _go2rtc_are(nume):
    """go2rtc-ul pornit cunoaste fluxul `nume` (deci are configuratia curenta incarcata)."""
    if not nume:
        return False
    try:
        r = requests.get(f"{C.GO2RTC_API}/api/streams", params={"src": nume}, timeout=5)
        return r.ok and "error" not in (r.json() or {})
    except Exception:
        return False


def rosteste(cam, text, repeat=1, speed=150):
    """Anunt vocal prin difuzorul camerei — implementarea unica e camctl.speak."""
    return drivers.speak(cam, text, repeat=repeat, speed=speed)


_clip_locks = {}                    # out_path -> Lock (o singura generare per clip)
_clip_locks_guard = threading.Lock()


def _clip_lock(out):
    with _clip_locks_guard:
        return _clip_locks.setdefault(out, threading.Lock())


def _build_clip(ch, t):
    """Genereaza (si cache-uieste) clipul -5s..+15s in jurul momentului. Intoarce
    (path, None) la succes sau (None, (mesaj, cod)) la esec.

    Un LOCK per clip + scriere ATOMICA (temp -> rename): deschiderea unui incident cere
    concurent /api/clip (video) SI /api/clip/boxes (chenare) pentru acelasi fisier — fara
    lock, doua ffmpeg scriau simultan in aceeasi iesire -> MP4 corupt (NAL invalide, nu se
    reda). Acum al doilea asteapta, gaseste clipul gata, si nimeni nu vede un fisier partial."""
    os.makedirs(os.path.join(ROOT, "data", "tmp"), exist_ok=True)
    out = os.path.join(ROOT, "data", "tmp", f"clip_{ch}_{int(t)}.mp4")
    if os.path.exists(out):
        return out, None
    with _clip_lock(out):
        if os.path.exists(out):        # alt fir l-a generat cat asteptam lock-ul
            return out, None
        t0, t1 = t - 5, t + 15
        lst = record.build_playlist(ch, t0, t1) if record.has_recordings(ch) else None
        hist = nvr_history_cam(ch)
        if lst:
            # -t explicit: inpoint/outpoint din playlist nu ajung cand segmentele se
            # suprapun (reconectare camera) — fara limita, clipul iesea cat tot segmentul
            src = ["-f", "concat", "-safe", "0", "-i", lst, "-t", str(int(t1 - t0))]
            h264 = record.video_codec(ch) == "h264"
        elif not hist:
            return None, ("nu exista inregistrare la acel moment", 404)
        else:  # istoricul de dinainte de migrare, de pe NVR
            fmt = lambda ts: time.strftime("%Y%m%dT%H%M%SZ", time.localtime(ts))
            src = ["-rtsp_transport", "tcp", "-i", hik_rtsp(hist, fmt(t0), fmt(t1)),
                   "-t", "20"]
            h264 = False  # NVR inregistreaza HEVC -> transcodam pentru browser
        mvf = masca.vf_cam(_cam_entry(ch))
        if mvf:
            h264 = False                 # cu masca nu se copiaza: se reincodeaza cu filtrul
        codec = (["-c:v", "copy"] if h264 else
                 (["-vf", mvf] if mvf else []) +
                 (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"] if HW
                  else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"]))
        # cand transcodam si avem GPU, decodam tot pe GPU (NVDEC); optiune de input,
        # deci sta inaintea sursei
        hw_dec = ["-hwaccel", "cuda"] if HW and not h264 else []
        # scriem intr-un fisier temporar, apoi rename atomic -> cititorii nu vad partiale
        tmp = f"{out}.{os.getpid()}.tmp.mp4"
        cmd = (["ffmpeg", "-y", "-nostdin", "-loglevel", "error"] + hw_dec + src
               + codec + ["-c:a", "aac", "-movflags", "+faststart", tmp])
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=180)
            if r.returncode != 0 or not os.path.exists(tmp) or os.path.getsize(tmp) < 1000:
                if os.path.exists(tmp):
                    os.remove(tmp)
                err = r.stderr.decode(errors="ignore")[-200:]
                if "400 Bad Request" in err:
                    err = "nu exista inregistrare la acel moment"
                return None, (f"clip esuat: {err}", 502)
            os.replace(tmp, out)       # atomic: apare intreg sau deloc
        except subprocess.TimeoutExpired:
            if os.path.exists(tmp):
                os.remove(tmp)
            return None, ("clip esuat: timeout", 504)
    return out, None


def _remember_desired(cam_id, want):
    """Retine setarile CERUTE pentru o camera. Unele camere isi reseteaza singure
    parametrii dupa un timp (observat: fps revine la maxim); pastram intentia ca sa
    putem verifica si repara automat."""
    # fps NU se retine: camerele revin singure la maximul lor dupa un timp, iar o
    # reaplicare la fiecare verificare ar insemna scrieri repetate, la nesfarsit.
    # Codecul si bitrate-ul raman aplicate, iar ele controleaza spatiul ocupat.
    keep = {k: want[k] for k in ("codec", "kbps") if want.get(k)}
    if not keep:
        return
    cams = load_ext()
    for c in cams:
        if c["id"] == cam_id:
            c.setdefault("stream_want", {}).update(keep)
            save_ext(cams)
            global _cams
            _cams = None
            return


def enforce_stream_settings():
    """Compara setarile reale cu cele cerute si le reaplica pe cele care au derivat.
    Ruleaza la 15 minute; nu atinge camerele care sunt deja corecte."""
    fixed = []
    for cam in load_ext():
        want = cam.get("stream_want")
        if not want or not drivers.este_hik(cam):
            continue
        try:
            cur = camctl.get_stream(cam, 101)
            drift = {k: v for k, v in want.items() if str(cur.get(k)) != str(v)}
            if drift:
                _set_stream_synced(cam, drift.get("codec"), drift.get("kbps"), drift.get("fps"))
                fixed.append(f"cam{cam['id']}({','.join(drift)})")
            time.sleep(0.4)
        except Exception as e:
            logare.info(f"[server] verificare setari cam{cam['id']}: {str(e)[:60]}")
    if fixed:
        logare.info(f"[server] setari refacute automat: {' '.join(fixed)}")
    t = __import__("threading").Timer(900, enforce_stream_settings)
    t.daemon = True
    t.start()


def _set_stream_synced(cam, codec=None, kbps=None, fps=None, keyframe=None, ch=101):
    """Scrie setarile de flux in camera SI pe canalul NVR corespunzator — altfel NVR-ul
    isi re-impune configuratia peste camerele proxied si schimbarea se pierde."""
    camctl.set_stream(cam, ch, codec=codec, kbps=kbps, fps=fps, keyframe=keyframe)
    if cam.get("nvr_channel") and not settings.nvr_retras():
        try:
            camctl.set_stream({"ip": C.NVR_IP, "user": C.NVR_USER, "pass": C.NVR_PASS},
                              cam["nvr_channel"] * 100 + (ch - 100),
                              codec=codec, kbps=kbps, fps=fps, keyframe=keyframe)
        except Exception as e:
            logare.info(f"[server] sync NVR cam{cam['id']}: {e}")


def daily_time_check():
    """O data pe zi: daca ceasul unei camere a derivat peste 30s sau fusul nu mai
    corespunde (trecerea la ora de vara/iarna), o resincronizeaza. Pragul era 2 min,
    dar OSD-ul camerei e si marcaj de proba pe inregistrari — cam7 ajunsese sa arate
    cu ~1 min in urma fata de axa reala a clipurilor (observat 2026-09-10)."""
    import datetime
    try:
        want_tz = camctl._tz_string(datetime.datetime.now().astimezone().utcoffset().total_seconds())
        host = settings.get("ntp_server") or None
        fixed = []
        for c in load_ext():
            if not drivers.este_hik(c):
                continue                    # ora se sincronizeaza doar prin ISAPI
            try:
                t = camctl.get_time(c)
                if t.get("tz") != want_tz or (t.get("drift") is not None and abs(t["drift"]) > 30):
                    camctl.set_time(c, ntp_host=host, use_ntp=bool(host))
                    fixed.append(c["id"])
                time.sleep(0.3)
            except Exception:
                pass
        if fixed:
            logare.info(f"[server] ora resincronizata pe camerele {fixed}")
    except Exception as e:
        logare.info(f"[server] verificare ora: {e}")
    t = __import__("threading").Timer(86400, daily_time_check)
    t.daemon = True
    t.start()


def _noise_event(cam_id, motiv, db):
    """Ce se intampla cand nivelul de zgomot depaseste regulile: poza, jurnal, notificare."""
    cam = _cam_entry(cam_id) or {}
    nume = cam.get("name") or f"camera {cam_id}"
    logare.info(f"[audio] {nume}: {motiv}")
    snaps.capture(cam_id, "zgomot")
    audit.log("alarma de zgomot", nume, {"nivel_db": db, "motiv": motiv})
    notify.send(f"🔊 {nume}: {motiv}", key=(cam_id, "zgomot"))
    try:
        hooks.emit("zgomot", cam_id, motiv=motiv, nivel_db=db)
    except Exception:
        pass


# ---- inregistrare stream playback / live-HD in go2rtc ----
def hik_rtsp(cam, start=None, end=None):
    """RTSP mainstream pentru o camera rezolvata (resolve_camera). Playback daca start dat.
    O camera ONVIF nu are track-uri de playback Hikvision: live-ul ei vine din driver."""
    if not start and cam.get("source") == "ext" and not drivers.este_hik(cam):
        return drivers.rtsp_main(cam)
    pw = urllib.parse.quote(cam["pass"], safe="")
    base = f"rtsp://{cam['user']}:{pw}@{cam['ip']}:{cam['port']}"
    if start:  # playback (track-ul de inregistrare)
        url = f"{base}/Streaming/tracks/{cam['main']}?starttime={start}"
        if end:
            url += f"&endtime={end}"
    else:      # live main stream
        url = f"{base}/Streaming/Channels/{cam['main']}"
    return url


def go2rtc_src(rtsp, transcode, audio=False, height=None, cam_id=None):
    """Construieste sursa go2rtc. transcode=True => HEVC->H264 (GPU daca exista).
    cam_id: camera cu masca de confidentialitate primeste template-ul ei (m<id>, vezi
    gen_go2rtc.py) si se reda la rezolutie nativa (treapta height nu se mai aplica).

    height: inaltimea la care scalam (720/1080...), 0 = nativ, None = globalul
    C.PLAYBACK_HEIGHT (comportamentul de dinainte de rezolutia adaptiva).

    Cu GPU (RTX 2000 Ada): decode NVDEC + encode NVENC, prin template-urile 'rtspn'/'h264n'.
    NVR-ul raporteaza un stream audio fantoma 'none' -> '-an' il elimina.
    """
    if not transcode:
        return rtsp
    if height is None:
        height = C.PLAYBACK_HEIGHT
    # HEVC -> H264 pentru browser. Decodare NVDEC (rtspn) la orice treapta, inclusiv cand
    # scalam (height=): "verdele de la NVDEC + scale" era de fapt '-fflags nobuffer' care
    # pierdea IDR-ul de la conectare (vezi gen_go2rtc.py). Encode mereu pe NVENC (h264n). Redarea in browser pe MSE (nu HLS: calea HLS
    # a go2rtc baga audio FLAC pe care Apple il refuza — vezi HD_MODE in app.js).
    # Sursele 'exec:' sunt respinse de API (insecure producer), deci ramanem pe 'ffmpeg:'.
    parts = [f"ffmpeg:{rtsp}"]
    mascata = cam_id is not None and masca.rects(_cam_entry(cam_id))
    if HW:
        parts.append("input=rtspn")
        parts.append(f"video=m{cam_id}" if mascata else "video=h264n")
    else:
        parts.append("video=h264")                # fara placa: libx264
    if height and not mascata:
        parts.append(f"height={height}")
    if audio:  # camerele standalone cu microfon au AAC pe mainstream -> il pastram
        parts.append("audio=aac")
    return "#".join(parts)


def register_stream(name, src):
    requests.put(f"{C.GO2RTC_API}/api/streams",
                 params={"name": name, "src": src}, timeout=10)


def _ts(compact):
    """'20260803T102030Z' -> epoch (interpretat ca ora locala, ca in restul interfetei)."""
    return time.mktime(time.strptime(compact, "%Y%m%dT%H%M%SZ"))


WM_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def _watermark_vf(ch, t0):
    """Filtru ffmpeg care graveaza in video identificarea camerei (sus) si ora reala
    care avanseaza cu inregistrarea (jos) — pentru probe care ajung in instanta."""
    cam = next((c for c in get_cameras() if c["id"] == ch), {})
    nume = re.sub(r"[':\\%]", " ", cam.get("name") or f"cam{ch}")[:40]
    ident = f"{C.NUME} · cam{ch} {nume}".replace(",", " ")
    box = "box=1:boxcolor=black@0.5:boxborderw=6:fontsize=20:fontcolor=white"
    f = WM_FONT.replace("/", "\\/")
    return (f"drawtext=fontfile={f}:text='{ident}':x=12:y=10:{box},"
            f"drawtext=fontfile={f}:text='%{{pts\\:localtime\\:{int(t0)}}}':"
            f"x=12:y=h-34:{box}")


def _run_ffmpeg(cmd, durata=None, timeout=600, job=None):
    """Ruleaza ffmpeg. Cu `job` (coada de joburi): progresul din `-progress pipe:1`
    (out_time raportat la `durata`) ajunge in job.progres, iar anularea jobului omoara
    procesul. Intoarce (returncode, stderr_text, expirat)."""
    if job is None:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return -9, "", True
        return r.returncode, _fara_parole(r.stderr), False
    cmd = list(cmd)
    cmd[1:1] = ["-nostats", "-progress", "pipe:1"]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    job.proc = p
    err = []
    t = threading.Thread(target=lambda: err.append(p.stderr.read()), daemon=True)
    t.start()
    limita = time.time() + timeout
    expirat = False
    for ln in p.stdout:
        if ln.startswith(b"out_time_us=") and durata:
            try:
                job.progres(min(99, int(int(ln[12:]) / 1e6 / durata * 100)))
            except ValueError:
                pass
        if time.time() > limita:
            expirat = True
            p.kill()
            break
    try:
        rc = p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        rc = -9
    t.join(timeout=5)
    job.proc = None
    return rc, _fara_parole(err[0] if err else b""), expirat


def _fara_parole(stderr):
    return logare.fara_parole(stderr)


def render_clip(ch, start, end, out_path, wm=False, job=None):
    """Randeaza intervalul [start,end] al camerei ch in out_path (MP4).
    Returneaza (ok, mesaj_eroare). Reutilizat de export si de pachetul de caz.
    `job` (optional): progres + anulare, vezi _run_ffmpeg."""
    t0, t1 = _ts(start), _ts(end)
    lst = record.build_playlist(ch, t0, t1) if record.has_recordings(ch) else None
    hist = nvr_history_cam(ch)
    # masca de confidentialitate se graveaza INAINTEA filigranului; cu masca nu se mai
    # poate copia fluxul — se reincodeaza ca la filigran
    mvf = masca.vf_cam(_cam_entry(ch))
    if wm and lst:
        # filigranul graveaza ora reala din pts; peste un gol de inregistrare ar minti
        # (clipul sare, ora nu) — pe material de proba refuzam, nu „reparam" in tacere
        g = record.goluri(ch, t0, t1)
        if g:
            lt = lambda x: time.strftime("%H:%M:%S", time.localtime(x))
            return False, ("intervalul contine goluri de inregistrare (" + ", ".join(f"{lt(a)}–{lt(b)}" for a, b in g[:3])
                           + "); filigranul ar arata o ora gresita — exporta fara filigran sau pe bucati fara gol")
    if wm or mvf:
        enc = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"] if HW
               else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
        vfilt = ["-vf", ",".join(x for x in (mvf, _watermark_vf(ch, t0) if wm else "") if x)] + enc
        wm = True
    if lst:
        if wm:
            vcodec = vfilt + ["-c:a", "aac"]
        else:  # HEVC copiat are nevoie de tag hvc1 ca sa mearga in playere/QuickTime
            vcodec = ["-c", "copy"] + (["-tag:v", "hvc1"]
                                       if record.video_codec(ch) == "hevc" else [])
        cmd = (["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", lst] + vcodec + [out_path])
    elif not hist:
        return False, "nu exista inregistrari in interval"
    else:
        vcodec = vfilt if wm else ["-map", "0:v", "-c", "copy", "-tag:v", "hvc1"]
        cmd = (["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                "-rtsp_transport", "tcp", "-i", hik_rtsp(hist, start, end)]
               + vcodec + ["-t", str(int(t1 - t0)), out_path])
    rc, err, expirat = _run_ffmpeg(cmd, durata=t1 - t0, timeout=(t1 - t0) * 2 + 120, job=job)
    if expirat:
        return False, "export esuat: timeout"
    if job is not None and job.anulat:
        return False, "anulat"
    if rc != 0 or not os.path.exists(out_path) or os.path.getsize(out_path) < 1000:
        err = err[-300:]
        if "400 Bad Request" in err:  # NVR-ul refuza intervalele fara inregistrare
            err = "nu exista inregistrare in intervalul ales (NVR-ul inregistreaza pe miscare)"
        return False, f"export esuat: {err or 'fisier gol'}"
    return True, None


def sanatate():
    """Starea de baza a serviciului: go2rtc raspunde, jurnalul se poate citi, inregistrarea
    scrie (macar o camera cu segment proaspat, daca exista camere). Folosita de /healthz
    si de bataia de inima catre systemd."""
    out = {"go2rtc": False, "jurnal": False, "inregistrare": None,
           "blocat": record.blocat()}   # directorul de inregistrari nu se poate scrie
    try:
        out["go2rtc"] = requests.get(f"{C.GO2RTC_API}/api/streams", timeout=4).ok
    except requests.RequestException:
        pass
    try:
        jurnal.query(limit=1)
        out["jurnal"] = True
    except Exception:
        pass
    try:
        cams = [c for c in load_ext() if c.get("record", "continuous") != "off"]
        if cams:
            ultim = max((record.last_write(c["id"]) or 0) for c in cams)
            out["inregistrare"] = time.time() - ultim < 600
    except Exception:
        out["inregistrare"] = False
    out["ok"] = (out["go2rtc"] and out["jurnal"] and out["inregistrare"] is not False
                 and not out["blocat"])
    return out


@app.route("/healthz")
def healthz():
    """Fara autentificare (monitorizare, load balancer): 200 cand e totul in regula, 503 altfel.
    Doar starea, nimic sensibil."""
    s = sanatate()
    return jsonify(s), (200 if s["ok"] else 503)


# ---- fisiere statice ----
_INDEX = {"mtime": 0, "text": ""}


def _index_sursa():
    """index.html, recitit cand se schimba pe disc (dezvoltare cu codul montat)."""
    cale = os.path.join(STATIC, "index.html")
    try:
        mtime = os.stat(cale).st_mtime
    except OSError:
        return ""
    if mtime != _INDEX["mtime"]:
        with open(cale, encoding="utf-8") as f:
            _INDEX["text"], _INDEX["mtime"] = f.read(), mtime
    return _INDEX["text"]


@app.route("/")
def index():
    """Pagina aplicatiei, tradusa pe server in limba cererii (i18n.pagina): browserul primeste
    direct textele limbii lui, fara sclipire; ETag pe continut, ca inainte (304 la recitire)."""
    corp = i18n.pagina(_index_sursa(), i18n.limba_curenta())
    # portul releului de flux (STREAM_PORT) ajunge in pagina: pe HTTP simplu browserul il
    # deschide direct, iar instalarile pe alte porturi nu mai nimeresc 8076
    corp = corp.replace('<meta charset="utf-8">',
                        f'<meta charset="utf-8">\n<meta name="ov-stream-port" content="{int(C.STREAM_PORT)}">', 1)
    resp = Response(corp, mimetype="text/html")
    resp.set_etag(hashlib.sha1(corp.encode()).hexdigest()[:20])
    return resp.make_conditional(request)


@app.route("/i18n/<limba>.json")
def i18n_dictionar(limba):
    """Dictionarul limbii pentru JavaScript (t() din 00-core.js): aplicatie + data/i18n."""
    if not i18n.limba_valida(limba):
        return jsonify({"error": t("limbă necunoscută")}), 404
    corp = json.dumps(i18n.dictionar(limba), ensure_ascii=False, separators=(",", ":"))
    resp = Response(corp, mimetype="application/json")
    resp.set_etag(hashlib.sha1(corp.encode()).hexdigest()[:20])
    return resp.make_conditional(request)


@app.route("/<path:p>")
def static_files(p):
    return send_from_directory(STATIC, p)


def _needs_probe(feat):
    """Re-sondam cand apar campuri noi de capabilitati (migrare automata la upgrade)."""
    return (feat is None or "zoom" not in feat or "smart" not in feat
            or (feat.get("mic") and "aac" not in feat))


def init_ext():
    """La pornire: completeaza capabilitatile camerelor care nu le au inca (probe + setup
    o singura data, persistat) si porneste watcherele de evenimente."""
    cams = load_ext()
    changed = False
    for cam in cams:
        if _needs_probe(cam.get("features")):
            try:
                cam["features"] = drivers.probe_features(cam)
                drivers.setup_camera(cam, cam["features"])
                changed = True
                logare.info(f"[server] features {cam['id']} ({cam.get('model')}): "
                      + ",".join(k for k, v in cam["features"].items() if v is True))
            except Exception as e:
                logare.info(f"[server] features {cam['ip']}: {e}")
    if changed:
        save_ext(cams)
    if changed:
        reload_streams()  # camerele cu difuzor primesc sursa isapi:// (backchannel)
    # evenimentele de alarma (miscare, intruziune) se urmaresc pe TOATE camerele,
    # nu doar pe cele standalone — altfel cele 16 de pe NVR n-ar avea badge-uri/poze
    sync_all_watchers(cams)


# ================= API pentru automatizari (Node-RED) =================
# Autentificare separata de sesiunea din browser: un jeton, trimis fie ca antet X-Token,
# fie ca ?token=. Asa un flux nu are nevoie de parola interfetei si il poti revoca singur.

def _token_ok():
    tok = settings.get("api_token")
    if not tok:
        return False
    # doar in antet: un jeton in query string ajunge in loguri, istoricul browserului, proxy-uri
    dat = request.headers.get("X-Token") or ""
    return hmac.compare_digest(str(dat), str(tok))


# ---- canalul intern nvr-ai -> nvr-web (evenimente de inferenta) ----
# nvr-ai produce evenimente dar NU detine motorul de reguli/MQTT (ar dubla actiunile);
# le trimite aici, iar web-ul le trece prin hooks.emit ca pe orice eveniment. Jeton
# partajat pe disc (data/ai_token), doar de pe localhost.
AI_TOKEN_FILE = os.path.join(ROOT, "data", "ai_token")


def ai_token():
    try:
        with open(AI_TOKEN_FILE) as f:
            t = f.read().strip()
            if t:
                return t
    except FileNotFoundError:
        pass
    t = secrets.token_hex(24)
    os.makedirs(os.path.dirname(AI_TOKEN_FILE), exist_ok=True)
    # 0o600: jetonul de control intern nu trebuie citibil de alti utilizatori de pe masina
    # (cine il citeste poate emite evenimente false / accesa canalul intern nvr-ai).
    fd = os.open(AI_TOKEN_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(t)
    return t


@app.route("/api/internal/emit", methods=["POST"])
def api_internal_emit():
    """nvr-ai trimite un eveniment de inferenta; web-ul il emite (jurnal+reguli+MQTT)."""
    # adresa socketului, nu cea din X-Forwarded-For (ProxyFix pastreaza originalul in environ)
    orig = (request.environ.get("werkzeug.proxy_fix.orig") or {}).get("REMOTE_ADDR") or request.remote_addr
    if orig not in ("127.0.0.1", "::1"):
        return jsonify({"error": "doar local"}), 403
    b = request.get_json(silent=True) or {}
    if not hmac.compare_digest(str(b.get("token", "")), ai_token()):
        return jsonify({"error": "jeton greșit"}), 401
    tip = b.get("tip")
    if not tip:
        return jsonify({"error": "tip lipsa"}), 400
    try:
        hooks.emit(tip, b.get("cam"), **(b.get("date") or {}))
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500
    return jsonify({"ok": True})


# (/api/internal/index a trait doar cat indexul era in web — #9 pas 3 l-a mutat in
# nvr-ai; acum web-ul e cel care TRIMITE indexarea, prin semantic.enqueue -> ai_client)


def _api_guard():
    if not _token_ok():
        return jsonify({"eroare": t("jeton lipsa sau greșit")}), 401
    return None


@app.route("/api/v1/camere")
def v1_camere():
    """Lista camerelor, cu ce stie fiecare sa faca — de aici isi ia fluxul id-urile."""
    if (r := _api_guard()):
        return r
    return jsonify([{"id": c["id"], "nume": c["name"], "grup": c.get("group"),
                     "ip": c["ip"], "poate": sorted(k for k, v in (c.get("features") or {}).items() if v)}
                    for c in load_ext()])


@app.route("/api/v1/stare")
def v1_stare():
    """Starea pe scurt: cine inregistreaza, ce detectii sunt active, spatiu."""
    if (r := _api_guard()):
        return r
    # starea detectiei vine din nvr-ai cand bucla ruleaza acolo (#9 pas 2)
    import ai_client
    det = (ai_client.detect_state() or {}).get("detect") if ai_client.remote() else None
    if det is None:
        det = detect.snapshot()
    return jsonify({"camere": continuity.live(),
                    "detectii_active": {k: v["labels"] for k, v in det.items() if v["labels"]},
                    "spatiu": continuity.forecast(),
                    "zgomot": audio_level.snapshot()})


@app.route("/api/v1/mod", methods=["GET", "POST"])
def v1_mod():
    """Modul acasa/plecat/noapte: GET il citeste, POST {"mod": "..."} il comuta — pentru
    Node-RED / PV-stack (prezenta telefonului in Wi-Fi, geofencing)."""
    if (r := _api_guard()):
        return r
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        try:
            st = mod.set(str(b.get("mod") or ""), cine="api", sursa="api")
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        audit.log("api", "mod", {"mod": st["mod"]})
        return jsonify({"ok": True, **st})
    return jsonify(mod.get())


@app.route("/api/v1/camera/<int:cam_id>/<actiune>", methods=["POST"])
def v1_actiune(cam_id, actiune):
    """Actiuni pe camera: sirena, strobo, releu, anunt, sunet.

    Corpul e JSON, toate campurile optionale:
      sirena  {"sunet": 1, "volum": 100, "repetari": 1}
      strobo  {"secunde": 10}
      releu   {"pornit": true}
      anunt   {"text": "Buna ziua, va rugam asteptati"}
    """
    if (r := _api_guard()):
        return r
    cam = _cam_entry(cam_id)
    if not cam:
        return jsonify({"eroare": t("camera inexistenta")}), 404
    d = request.get_json(silent=True) or {}
    f = cam.get("features") or {}
    try:
        if actiune == "sirena":
            if not f.get("siren"):
                return jsonify({"eroare": t("camera nu are sirena")}), 400
            snd = str(d.get("sunet", 1))
            drivers.trigger_siren(cam, audio_id=int(str(snd).lstrip("c")),
                                  volume=int(d.get("volum", 100)),
                                  times=int(d.get("repetari", 1)),
                                  custom=str(snd).startswith("c"))
            hooks.emit("sirena", cam_id, motiv=["api"], automat=False)
        elif actiune == "strobo":
            if not f.get("strobe"):
                return jsonify({"eroare": t("camera nu are stroboscop")}), 400
            drivers.set_strobe(cam, int(d.get("secunde", 10)))
        elif actiune == "releu":
            if not f.get("io_out"):
                return jsonify({"eroare": t("camera nu are ieșire de releu")}), 400
            drivers.set_output(cam, int(d.get("port") or 1), bool(d.get("pornit", True)))
        elif actiune == "anunt":
            if not f.get("talk"):
                return jsonify({"eroare": t("camera nu are difuzor")}), 400
            text = (d.get("text") or "").strip()
            if not text:
                return jsonify({"eroare": t("lipsește textul")}), 400
            rosteste(cam, text, int(d.get("repetari") or 1))
        else:
            return jsonify({"eroare": t("acțiune necunoscută: {actiune}", actiune=actiune)}), 400
    except Exception as e:
        return jsonify({"eroare": str(e)[:200]}), 502
    audit.log("api", f"cam{cam_id}", {"actiune": actiune, **d})
    return jsonify({"ok": True, "camera": cam_id, "actiune": actiune})


@app.route("/api/v1/camera/<int:cam_id>/poza")
def v1_poza(cam_id):
    """Cadrul curent al camerei, ca JPEG — pentru atasat intr-un mesaj din flux."""
    if (r := _api_guard()):
        return r
    try:
        r = requests.get(f"{C.GO2RTC_API}/api/frame.jpeg",
                         params={"src": f"cam{cam_id}"}, timeout=10)
        if r.ok and r.content:
            return Response(r.content, mimetype="image/jpeg")
    except requests.RequestException:
        pass
    return jsonify({"eroare": t("nu am putut lua cadrul")}), 502


def _register_blueprints():
    """Inregistrarea blueprint-urilor se face la RUNTIME (nu la import), ca sa nu creeze
    un ciclu: server importa routes_*, iar routes_* fac `from server import <helper>`.
    Cand se apeleaza asta, server e deja complet incarcat (helper-urile exista)."""
    import routes_ai
    import routes_analiza
    import routes_analytics
    import routes_auth
    import routes_automation
    import routes_cameras
    import routes_date
    import routes_incidents
    import routes_jobs
    import routes_marcaje
    import routes_media
    import routes_operare
    import routes_push
    import routes_system
    app.register_blueprint(routes_auth.bp)
    app.register_blueprint(routes_incidents.bp)
    app.register_blueprint(routes_marcaje.bp)
    app.register_blueprint(routes_jobs.bp)
    app.register_blueprint(routes_push.bp)
    app.register_blueprint(routes_operare.bp)
    app.register_blueprint(routes_analiza.bp)
    app.register_blueprint(routes_date.bp)
    app.register_blueprint(routes_automation.bp)
    app.register_blueprint(routes_ai.bp)
    app.register_blueprint(routes_cameras.bp)
    app.register_blueprint(routes_media.bp)
    app.register_blueprint(routes_analytics.bp)
    app.register_blueprint(routes_system.bp)


def main():
    from waitress import serve
    _register_blueprints()
    ai_token()                # creeaza jetonul partajat cu nvr-ai daca lipseste
    init_ext()
    jurnal.deschide()         # jurnalul SQLite (prima data: migrarea events.jsonl, ~10 s)
    jobs.start()              # lucratorii cozii de joburi (export, clip, dosar, camera noua)
    push.camere_fn = get_cameras   # notificarile push respecta restrictia pe camere a contului
    detect.sync()             # porneste workerii YOLO pentru camerele cu detectia activata
    audio_level.sync(load_ext(), _noise_event)   # masurarea zgomotului pe camerele cu microfon
    snaps.prune()             # curata snapshot-urile vechi (>30 zile), reprogramat zilnic
    # transformers se incarca AICI, in firul principal, inainte de warmup-uri:
    # semantic si describe il importa altfel simultan din thread-uri diferite si
    # unul prinde modulul pe jumatate initializat (ImportError pe clase lazy —
    # s-a intamplat de doua ori, cu AutoModel si AutoModelForImageTextToText)
    # cand ai_service e pornit, Face ID/LPR/VLM traiesc in nvr-ai (#9 pas 1) — web-ul
    # nu le mai preincarca; raman fallback lazy pentru cazul in care nvr-ai cade
    _ai_remote = bool(settings.get("ai_service"))
    if (semantic.enabled() or describe.enabled()) and not _ai_remote:
        import transformers  # noqa: F401
    if not _ai_remote:
        faces.warmup_async()  # incarca modelul de fete daca Face ID e pornit
        plates.warmup_async() # incarca modelul LPR daca citirea numerelor e pornita
        semantic.warmup_async()  # ... si SigLIP; toate raman fallback lazy cand nvr-ai le tine
    mqtt_bridge.warmup()      # conecteaza puntea MQTT daca e pornita

    # VLM-ul de semnalmente se incarca ABIA DUPA SigLIP: from_pretrained(bf16)
    # tine dtype-ul implicit global pe bfloat16 cat dureaza incarcarea, iar un
    # SigLIP incarcat in acea fereastra iese cu greutati bf16 si toate cautarile
    # pica. Ordinea stricta inchide fereastra; guard-ul din describe o si repara.
    def _describe_dupa_semantic():
        try:
            if semantic.enabled() and not _ai_remote:
                semantic._get_model()     # se intoarce cand siglip e complet incarcat
        except Exception as e:
            logare.info(f"[server] warmup semantic: {str(e)[:80]}")
        try:
            if describe.enabled() and not _ai_remote:
                describe._get_model()     # fereastra bf16 se inchide abia aici
        except Exception as e:
            logare.info(f"[server] warmup describe: {str(e)[:80]}")
        # urmarirea porneste DUPA nu inainte: primul ei cadru a prins odata fereastra
        # bf16 a incarcarii Qwen ("Got unsupported ScalarType BFloat16")
        track.sync()
    _dt = __import__("threading").Thread(target=_describe_dupa_semantic, daemon=True)
    _dt.start()
    notify.start_watchdog()   # camera/NVR cazut(a)/revenit(a), disc plin -> Telegram
    mod.start()               # noaptea automata (daca e pornita) + modul pe MQTT
    telegram_bot.start()      # /acasa /plecat /noapte /stare (daca e pornit din Setari)
    backup_nocturn.start()    # copia criptata zilnica la backup_ora (+ trimitere externa)
    import digest
    digest.start()            # rezumatul zilei pe Telegram, la digest_ora (daca e pornit)
    reencode.tick()           # re-encodare inregistrari vechi (daca e activata din Setari)
    enforce_stream_settings() # reaplica setarile de flux care au derivat (la 15 min)
    # verificarea orei trece prin toate camerele -> o programam dupa pornire,
    # ca serverul sa raspunda imediat
    _t = __import__("threading").Timer(120, daily_time_check)
    _t.daemon = True
    _t.start()
    # inregistrarea continua ruleaza in serviciul separat nvr-record (app/record_main.py)
    logare.info(f"[server] http://{C.WEB_HOST}:{C.WEB_PORT}")
    # go2rtc asculta doar pe localhost; browserul ajunge la el prin releul autentificat
    g = urllib.parse.urlsplit(C.GO2RTC_API)
    stream_proxy.start(app, current_user, _visible_ids, (g.hostname, g.port or 1984),
                       C.WEB_HOST, getattr(C, "STREAM_PORT", 8076))
    # waitress sterge implicit antetele X-Forwarded-* de la surse „neincredere" (toate);
    # le lasam sa treaca: _DinSpateleProxyului le crede doar de la proxy-ul de incredere
    # systemd: READY cand pornim sa servim; WATCHDOG=1 la 30 s cat timp go2rtc si jurnalul
    # raspund (Type=notify + WatchdogSec in unitate; fara NOTIFY_SOCKET e no-op)
    sdnotify.ready()
    sdnotify.start_watchdog(lambda: (lambda s: s["go2rtc"] and s["jurnal"])(sanatate()))
    serve(app, host=C.WEB_HOST, port=C.WEB_PORT, threads=16,
          clear_untrusted_proxy_headers=False)
