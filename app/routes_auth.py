#!/usr/bin/env python3
"""Blueprint: autentificare — login/setup/logout + conturi (/api/me, /api/users).

Al saselea grup extras din server.py (audit #8). MIEZUL auth ramane in `server`:
guard-ul global `@app.before_request` (acopera automat toate blueprint-urile),
`current_user`, `_visible_ids`, VIEWER_GET/OPERATOR_ACTIONS si cheia de sesiune —
sunt middleware folosit peste tot, nu rute. Aici stau doar rutele si helper-ele
lor private (formularele HTML, rate-limit-ul pe login, pornirea sesiunii).

Autentificarea in doi pasi (TOTP, totp.py): dupa parola corecta, un cont cu `totp` nu
primeste sesiune, ci un „pre-bilet" de 5 minute in cookie (`pre2fa`) si formularul de
cod; /login/2fa verifica codul (sau un cod de recuperare) si abia atunci porneste sesiunea.
Sesiunea poarta versiunea contului (`v`): current_user o compara cu `sess_ver` din
users.json, deci „deconecteaza toate dispozitivele" sau o parola noua taie TOATE
cookie-urile vechi, nu doar pe cel curent.
"""
import base64
import secrets
import threading
import time

from markupsafe import escape
from flask import Blueprint, g, jsonify, redirect, request, session

import audit
import i18n
import totp
import users
from i18n import t
from server import _visible_ids, current_user
import config as C

PRE2FA_SEC = 300      # dupa parola corecta, codul trebuie sa vina in 5 minute
# secretul TOTP in curs de activare, pe utilizator: (secret, moment). Sta in memorie, NU in
# cookie — interfata face polling (evenimente, incidente, mod) si doua raspunsuri care se
# suprapun ar rescrie cookie-ul cu o sesiune fara secret („intai genereaza codul QR").
_totp_nou = {}
TOTP_NOU_SEC = 600

bp = Blueprint("auth", __name__)

_AUTH_CSS = """<style>body{margin:0;font-family:system-ui;background:#14171c;color:#e6e8eb;
display:flex;align-items:center;justify-content:center;min-height:100vh}form{background:#1c2128;
border:1px solid #2d333b;border-radius:10px;padding:28px;width:300px;text-align:center}
h1{font-size:17px;margin:0 0 16px}input{width:100%;box-sizing:border-box;background:#22272e;
color:#e6e8eb;border:1px solid #2d333b;border-radius:6px;padding:10px;font-size:15px;
margin-bottom:12px}button{width:100%;background:#316dca;color:#fff;border:0;padding:10px;
border-radius:6px;font-size:15px;cursor:pointer}.err{color:#f85149;font-size:13px;
min-height:18px;margin:0 0 8px}p.hint{color:#8b949e;font-size:12px}</style>"""

LOGIN_HTML = ("""<!DOCTYPE html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>NVR — login</title>"""
              + _AUTH_CSS + """</head><body>
<form method="post" action="/login"><h1>🎥 """ + C.NUME + """</h1><p class="err">{err}</p>
<input name="user" placeholder="Utilizator" autofocus autocomplete="username">
<input type="password" name="password" placeholder="Parola" autocomplete="current-password">
<button type="submit">Intra</button>
{limbi}
<p class="hint"><a href="https://openvigil.dev" rel="noopener" style="color:#8b949e">OpenVigil</a> · openvigil.dev</p></form></body></html>""")

SETUP_HTML = ("""<!DOCTYPE html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>NVR — primul cont</title>"""
              + _AUTH_CSS + """</head><body>
<form method="post" action="/setup"><h1>🎥 """ + C.NUME + """ — <span>primul cont</span></h1>
<p class="hint">Nu exista inca niciun utilizator. Creeaza contul de administrator.</p>
<p class="err">{err}</p>
<input name="user" placeholder="Utilizator" value="admin" autocomplete="username">
<input type="password" name="password" placeholder="Parola (minim 6 caractere)"
 autocomplete="new-password" autofocus>
<input type="password" name="password2" placeholder="Parola inca o data"
 autocomplete="new-password">
<button type="submit">Creeaza contul</button>
{limbi}
<p class="hint"><a href="https://openvigil.dev" rel="noopener" style="color:#8b949e">OpenVigil</a> · openvigil.dev</p></form></body></html>""")

TOTP_HTML = ("""<!DOCTYPE html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>NVR — cod</title>"""
             + _AUTH_CSS + """</head><body>
<form method="post" action="/login/2fa"><h1>🎥 """ + C.NUME + """ — <span>al doilea pas</span></h1>
<p class="hint">Scrie codul din aplicatia de autentificare (sau un cod de recuperare).</p>
<p class="err">{err}</p>
<input name="cod" placeholder="Cod de 6 cifre" autofocus inputmode="numeric"
 autocomplete="one-time-code" pattern="[0-9a-z ]*">
<button type="submit">Intra</button>
<p class="hint"><a href="/login" style="color:#8b949e">Inapoi la login</a></p></form></body></html>""")

# selectorul de limba de pe formulare: ?lang=xx ramane in cookie (server.py)
_LIMBI_HTML = ('<p class="hint lang">' + " · ".join(
    f'<a href="?lang={c}" style="color:#8b949e">{escape(n)}</a>' for c, n in i18n.disponibile()) + "</p>")


def _pagina(sablon, **camp):
    """Formularul tradus in limba cererii, cu campurile {err}/{rez} completate (textul
    mesajelor e deja tradus de apelant; {rez} e HTML de incredere construit aici)."""
    limba = i18n.limba_curenta()
    corp = i18n.pagina(sablon, limba).replace("{limbi}", _LIMBI_HTML)
    for k, v in camp.items():
        corp = corp.replace("{" + k + "}", v)
    return corp


# incercari esuate de login per IP: dupa 8 in 15 minute, 429 pana se scurge fereastra
_login_fail = {}
# /verifica e publica: cel mult 2 verificari deodata si 20 pe ora de la acelasi IP
_verif_sem = threading.BoundedSemaphore(2)
_verif_ip = {}


def _too_many_fails(ip):
    now = time.time()
    _login_fail[ip] = [t for t in _login_fail.get(ip, []) if now - t < 900]
    return len(_login_fail[ip]) >= 8


def _login_ok(u):
    session.clear()
    session.permanent = True
    session["user"] = u["user"]
    session["v"] = users.sess_ver(u)
    session["csrf"] = secrets.token_hex(32)


def _esec(ip, actiune, name):
    """Esec de login: numarat per IP (8 in 15 minute = 429). Fara sleep: o intarziere pe
    fir tinea ocupat unul din cele 16 fire waitress la fiecare incercare esuata."""
    _login_fail.setdefault(ip, []).append(time.time())
    audit.log(actiune, name)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if not users.load():
        return redirect("/setup")
    if current_user():
        return redirect("/")
    if request.method == "POST":
        ip = request.remote_addr
        if _too_many_fails(ip):
            audit.log("login blocat (prea multe încercări)")
            return _pagina(LOGIN_HTML, err=t("Prea multe încercări — așteaptă 15 minute")), 429
        name = (request.form.get("user") or "").strip()
        u = users.verify(name, request.form.get("password", ""))
        if u and u.get("totp"):
            # parola e buna, dar sesiunea porneste abia dupa cod: cookie-ul tine doar un
            # pre-bilet cu termen, fara `user` (current_user ramane None pana atunci)
            session.clear()
            session["pre2fa"] = {"user": name, "t": time.time()}
            audit.log("login: parola corectă, se așteaptă codul", name)
            return _pagina(TOTP_HTML, err="")
        if u:
            _login_fail.pop(ip, None)
            _login_ok(u)
            audit.log("login reușit", name, who=name)
            return redirect("/")
        _esec(ip, "login esuat", name)
        return _pagina(LOGIN_HTML, err=t("Utilizator sau parola greșite"))
    return _pagina(LOGIN_HTML, err="")


@bp.route("/login/2fa", methods=["GET", "POST"])
def login_2fa():
    """Al doilea pas: codul TOTP (sau unul de recuperare) pentru pre-biletul din cookie."""
    pre = session.get("pre2fa") or {}
    name = pre.get("user")
    if not name or time.time() - float(pre.get("t") or 0) > PRE2FA_SEC:
        session.pop("pre2fa", None)
        return redirect("/login")
    if request.method == "GET":
        return _pagina(TOTP_HTML, err="")
    ip = request.remote_addr
    if _too_many_fails(ip):
        audit.log("login blocat (prea multe încercări)", name)
        return _pagina(TOTP_HTML, err=t("Prea multe încercări — așteaptă 15 minute")), 429
    u = users.get(name)
    tp = (u or {}).get("totp")
    if not u or not tp:
        session.pop("pre2fa", None)
        return redirect("/login")
    cod = (request.form.get("cod") or "").strip()
    ctr = totp.verifica(tp["secret"], cod, dupa=int(tp.get("ultimul") or 0))
    if ctr is not None and users.totp_folosit(name, ctr):
        cum = "cod"
    elif users.recuperare_folosita(name, cod):
        cum = "cod de recuperare"
    else:
        _esec(ip, "cod 2FA gresit", name)
        return _pagina(TOTP_HTML, err=t("Cod greșit sau deja folosit"))
    _login_fail.pop(ip, None)
    _login_ok(u)
    audit.log(f"login reușit ({cum})", name, who=name)
    return redirect("/")


@bp.route("/setup", methods=["GET", "POST"])
def setup():
    """Prima pornire: creeaza contul de administrator. Dispare dupa ce exista conturi."""
    if users.load():
        return redirect("/login")
    if request.method == "POST":
        name = (request.form.get("user") or "").strip() or "admin"
        pw = request.form.get("password") or ""
        if pw != (request.form.get("password2") or ""):
            return _pagina(SETUP_HTML, err=t("Parolele nu coincid"))
        try:
            u = users.upsert(name, password=pw, role="admin")
        except ValueError as e:
            return _pagina(SETUP_HTML, err=str(escape(t(str(e)))))
        _login_ok(u)
        audit.log("cont administrator creat", name, who=name)
        return redirect("/")
    return _pagina(SETUP_HTML, err="")


VERIFICA_HTML = ("""<!DOCTYPE html><html lang="ro"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>NVR — verificarea probelor</title>"""
                 + _AUTH_CSS + """<style>form{max-width:520px}.rez{padding:12px 14px;border-radius:8px;margin:12px 0;font-size:15px;line-height:1.5}
.ok{background:#1d3323;color:#8fd19e}.nu{background:#3a1f1d;color:#ff8a80}.hint{font-size:13px;opacity:.75}
code{font-size:12px;word-break:break-all}</style></head><body>
<form method="post" action="/verifica" enctype="multipart/form-data"><h1>🎥 """ + C.NUME + """ — <span>verificarea probelor</span></h1>
<p class="hint">Urcă un fișier MP4 exportat din acest NVR sau lipește amprenta lui SHA-256.
Primești confirmarea că a fost produs aici, când, de pe ce cameră, și că nu a fost modificat de atunci.</p>
{rez}
<input type="file" name="fisier" accept="video/mp4,.mp4">
<input name="sha256" placeholder="sau SHA-256 (64 de caractere hex)" autocomplete="off" pattern="[0-9a-fA-F]{64}">
<button type="submit">Verifică</button>
<p class="hint">Amprenta se calculează pe server și nu se păstrează fișierul. Pe calculatorul tău: <code>sha256sum fisier.mp4</code>.</p>
{limbi}
</form></body></html>""")


@bp.route("/verifica", methods=["GET", "POST"])
def verifica():
    """Pagina publica (fara login): un MP4 exportat sau un hash -> e al nostru si neatins?"""
    if request.method == "GET":
        return _pagina(VERIFICA_HTML, rez="")
    # inainte de a citi corpul: marimea anuntata, rata per IP, cate verificari ruleaza deodata
    if (request.content_length or 0) > 2 * 1024 ** 3:
        return _pagina(VERIFICA_HTML, rez='<div class="rez nu">' + t("Fișierul e prea mare pentru verificare pe web (peste 2 GB): folosește {cmd} și lipește amprenta.", cmd="<code>sha256sum</code>") + '</div>'), 413
    ip, acum = request.remote_addr, time.time()
    _verif_ip[ip] = [x for x in _verif_ip.get(ip, []) if acum - x < 3600] + [acum]
    if len(_verif_ip[ip]) > 20 or not _verif_sem.acquire(blocking=False):
        return _pagina(VERIFICA_HTML, rez='<div class="rez nu">' + t("Prea multe verificări deodată; încearcă din nou în câteva minute.") + '</div>'), 429
    try:
        return _verifica_corp()
    finally:
        _verif_sem.release()


def _verifica_corp():
    import html as _html
    import verificare
    from server import get_cameras
    sha = (request.form.get("sha256") or "").strip().lower()
    f = request.files.get("fisier")
    octeti = None
    if f and f.filename:
        sha, octeti = verificare.hash_flux(f.stream)
        if sha is None:
            return _pagina(VERIFICA_HTML, rez='<div class="rez nu">' + t("Fișierul e prea mare pentru verificare pe web (peste 2 GB): folosește {cmd} și lipește amprenta.", cmd="<code>sha256sum</code>") + '</div>'), 413
    if not sha:
        return _pagina(VERIFICA_HTML, rez='<div class="rez nu">' + t("Alege un fișier sau lipește o amprentă.") + '</div>'), 400
    nume = {c["id"]: c.get("name") or f"cam {c['id']}" for c in get_cameras()}
    r = verificare.cauta(sha, nume)
    e = _html.escape
    if r.get("gasit"):
        if r["fel"] == "segment":
            txt = ('<div class="rez ok">✅ <b>' + t("Segment brut înregistrat de acest NVR") + "</b> "
                   + t("de pe camera {cam}, fișierul {fisier}, amprentă luată la închiderea segmentului ({cand}).",
                       cam=f'<b>{e(r["cam_nume"])}</b>', fisier=f'<code>{e(r["nume"] or "")}</code>', cand=e(r["t"] or ""))
                   + " " + t("Conținutul este identic cu cel de atunci.") + f"<br><code>{sha}</code></div>")
        else:
            txt = ('<div class="rez ok">✅ <b>' + t("Export produs de acest NVR") + "</b> "
                   + t("pe {cand}, camera {cam}, intervalul {start} – {end}", cand=e(r["t"]), cam=f'<b>{e(r["cam_nume"])}</b>',
                       start=e(r.get("start") or ""), end=e(r.get("end") or ""))
                   + (", " + t("cerut de {cine}", cine=e(r["cine"])) if r.get("cine") else "")
                   + ". " + t("Conținutul este identic cu fișierul livrat atunci.") + f"<br><code>{sha}</code></div>")
    else:
        txt = ('<div class="rez nu">❌ <b>' + t("Amprentă necunoscută") + "</b>: "
               + t("fișierul nu a fost produs de acest NVR, a fost modificat după export, sau e mai vechi decât registrul ({zi}).", zi="2026-10-25")
               + f"<br><code>{e(sha)}</code></div>")
    try:
        audit.log("verificare publică de probă", sha[:16], detail={"gasit": bool(r.get("gasit")), "octeti": octeti}, who="public")
    except Exception:
        pass
    return _pagina(VERIFICA_HTML, rez=txt)


@bp.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@bp.route("/api/auth/forward")
def api_auth_forward():
    """forwardAuth pentru Traefik: 200 (+ X-Nvr-User/X-Nvr-Role) cand cererea are o sesiune NVR
    valida, altfel 401. Cu COOKIE_DOMAIN setat in config.py (ex. ".exemplu.ro"), sesiunea
    ajunge si la alte subdomenii, iar Traefik poate pune un alt serviciu fara conturi proprii
    in spatele login-ului NVR."""
    u = current_user()
    if not u:
        return jsonify({"error": t("neautentificat")}), 401
    r = jsonify({"ok": True, "user": u["user"], "role": u["role"]})
    r.headers["X-Nvr-User"] = u["user"]
    r.headers["X-Nvr-Role"] = u["role"]
    return r


@bp.route("/api/me")
def api_me():
    u = current_user()
    vis = _visible_ids()
    return jsonify({"user": u["user"], "role": u["role"], "csrf": session.get("csrf"),
                    "cameras": sorted(vis) if vis is not None else None,
                    "totp": bool(u.get("totp")), "https": request.is_secure,
                    "lang": i18n.limba_curenta(), "lang_cont": u.get("lang"),
                    "limbi": [{"cod": c, "nume": n} for c, n in i18n.disponibile()]})


@bp.route("/api/me/limba", methods=["POST"])
def api_me_limba():
    """Limba interfetei pentru contul curent (Contul meu); goala = dupa browser. Ramane si in
    cookie, ca formularul de login si pagina de dupa sa fie deja in limba aleasa."""
    u = current_user()
    b = request.get_json(silent=True) or {}
    limba = (b.get("limba") or "").strip()
    if limba and not i18n.limba_valida(limba):
        return jsonify({"error": t("limbă necunoscută")}), 400
    users.set_lang(u["user"], limba or None)
    g.limba = None
    g.limba_cookie = limba          # gol = se sterge cookie-ul, ramane browserul
    audit.log("limba contului schimbată", u["user"], {"limba": limba or "auto"})
    return jsonify({"ok": True, "lang": limba or None})


@bp.route("/api/me/deconectare", methods=["POST"])
def api_me_deconectare():
    """Deconecteaza TOATE dispozitivele contului curent (telefon pierdut, laptop strain);
    sesiunea de aici ramane, cu versiunea noua."""
    u = current_user()
    session["v"] = users.revoke(u["user"])
    audit.log("sesiuni revocate (toate dispozitivele)", u["user"])
    return jsonify({"ok": True})


@bp.route("/api/me/2fa/pregateste", methods=["POST"])
def api_2fa_pregateste():
    """Pasul 1: un secret nou (tinut doar in sesiune pana la confirmare) + QR-ul lui."""
    u = current_user()
    secret = totp.secret_nou()
    _totp_nou[u["user"]] = (secret, time.time())
    uri = totp.uri(secret, u["user"])
    svg = totp.qr_svg(uri)
    return jsonify({"secret": secret, "uri": uri,
                    "qr": "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
                    if svg else None})


@bp.route("/api/me/2fa/activeaza", methods=["POST"])
def api_2fa_activeaza():
    """Pasul 2: codul din aplicatie dovedeste ca secretul a fost scanat; abia atunci se
    salveaza. Raspunsul aduce codurile de recuperare — singura data cand se vad."""
    u = current_user()
    secret, ts = _totp_nou.get(u["user"]) or (None, 0)
    b = request.get_json(silent=True) or {}
    if not secret or time.time() - ts > TOTP_NOU_SEC:
        _totp_nou.pop(u["user"], None)
        return jsonify({"error": t("întâi generează codul QR (e valabil 10 minute)")}), 400
    if totp.verifica(secret, str(b.get("cod") or "")) is None:
        return jsonify({"error": t("cod greșit — verifică ora telefonului și încearcă iar")}), 400
    coduri = totp.coduri_recuperare()
    users.set_totp(u["user"], secret, coduri)
    _totp_nou.pop(u["user"], None)
    audit.log("2FA activat", u["user"])
    return jsonify({"ok": True, "recuperare": coduri})


@bp.route("/api/me/2fa/dezactiveaza", methods=["POST"])
def api_2fa_dezactiveaza():
    """Scoate 2FA de pe contul curent — cere parola din nou, ca un laptop lasat deschis
    sa nu ajunga."""
    u = current_user()
    b = request.get_json(silent=True) or {}
    if not users.verify(u["user"], str(b.get("parola") or "")):
        return jsonify({"error": t("parola greșită")}), 400
    users.clear_totp(u["user"])
    audit.log("2FA dezactivat", u["user"])
    return jsonify({"ok": True})


@bp.route("/api/users", methods=["GET", "POST"])
def api_users():
    """Administrarea conturilor (guard-ul lasa doar adminul sa ajunga aici)."""
    if request.method == "GET":
        return jsonify([users.public(x) for x in users.load()])
    b = request.get_json(silent=True) or {}
    me = current_user()          # INAINTE de upsert: parola noua imi schimba versiunea
    try:
        u = users.upsert((b.get("user") or "").strip(), password=b.get("pass") or None,
                         role=b.get("role"), cameras=b.get("cameras"),
                         groups=b.get("groups"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if b.get("pass") and me and me["user"] == u["user"]:
        session["v"] = users.sess_ver(u)     # parola mea noua nu ma scoate de aici
    audit.log("utilizator salvat", u["user"], {"rol": u["role"]})
    return jsonify({"ok": True, "user": users.public(u)})


@bp.route("/api/users/<name>/revoca", methods=["POST"])
def api_user_revoca(name):
    """Adminul deconecteaza toate dispozitivele unui cont (ex. paznic plecat)."""
    try:
        v = users.revoke(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    me = current_user()
    if me and me["user"] == name:
        session["v"] = v
    audit.log("sesiuni revocate de administrator", name)
    return jsonify({"ok": True})


@bp.route("/api/users/<name>/2fa", methods=["DELETE"])
def api_user_2fa_delete(name):
    """Adminul scoate 2FA de pe un cont (telefon pierdut, fara coduri de recuperare)."""
    try:
        avea = users.clear_totp(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("2FA scos de administrator", name)
    return jsonify({"ok": True, "avea": avea})


@bp.route("/api/users/<name>", methods=["DELETE"])
def api_user_delete(name):
    me = current_user()
    if me and me["user"] == name:
        return jsonify({"error": t("nu îți poți șterge propriul cont")}), 400
    try:
        users.delete(name)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("utilizator sters", name)
    return jsonify({"ok": True})
