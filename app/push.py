#!/usr/bin/env python3
"""Web Push catre PWA: incidentele ajung pe telefon ca notificare nativa.

Browserul (Chrome/Firefox/Safari 16+) se aboneaza la serviciul de push al furnizorului
lui si ne da un `endpoint` + doua chei; noi semnam fiecare mesaj cu cheia VAPID a
instalarii (data/push_vapid.json, generata o data) si il criptam pentru abonat. Nimic nu
trece in clar prin Google/Mozilla/Apple, si nu avem nevoie de niciun cont acolo.
Cerinte: pagina servita pe HTTPS (sau localhost) si permisiunea de notificari.

Abonamentele stau in tabelul `push_abonamente` din baza jurnalului (jurnal.conexiune()),
cu utilizatorul si nivelul ales (critic / atentie). La trimitere, un eveniment ajunge doar
la abonatii care AU VOIE sa vada camera (users.allowed_ids), exact ca in interfata.
Un endpoint care raspunde 404/410 (abonament revocat de browser) se sterge singur.
Telegram ramane canalul de rezerva: nu depinde de niciun telefon anume.
"""
import base64
import json
import os
import threading
import time

import jurnal
import store
import users
import logare
import config as C
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAPID_FILE = os.path.join(ROOT, "data", "push_vapid.json")
NIVELURI = ("critic", "atentie")
TTL_EVENIMENT = 600        # un incident vechi de 10 min nu mai merita livrat
TIP_RO = {"detectie": "Detectie", "persoana": "Persoana", "persoana_necunoscuta": "Fata necunoscuta",
          "fata_falsa": "Fata FALSA (spoofing)", "descriere": "Semnalmente", "obiect": "Obiect urmarit",
          "numar": "Numar", "alarma_camera": "Alarma camera", "zgomot": "Zgomot", "sirena": "Sirena",
          "regula": "Regula", "camera_offline": "Camera cazuta", "camera_online": "Camera revenita",
          "gol_inregistrare": "Gol inregistrare", "intrus": "INTRUS confirmat",
          "trecere": "Trecere linie", "intrare_zona": "Intrare in zona",
          "obiect_nou": "Obiect nou aparut", "obiect_disparut": "Obiect disparut",
          "parcare_inceput": "Vehicul parcat", "parcare_sfarsit": "Vehicul plecat din parcare",
          "episod": "Episod de prezenta"}
_SEV_RANK = {"info": 0, "atentie": 1, "critic": 2}

# serverul le seteaza la pornire: lista camerelor (pentru restrictia pe camere si nume)
camere_fn = lambda: []          # noqa: E731
_lock = threading.Lock()
_gata = None
_vapid = None
# lasat deschis pentru teste: inlocuiesc trimiterea reala cu o functie care inregistreaza
_webpush = None


# ---- cheile VAPID ----
def _incarca_vapid():
    global _vapid
    if _vapid:
        return _vapid
    with _lock:
        if _vapid:
            return _vapid
        d = store.load_json(VAPID_FILE, None)
        if not d or not d.get("private") or not d.get("public"):
            d = _genereaza_vapid()
            if d:
                store.atomic_write_json(VAPID_FILE, d, mode=0o600)
        _vapid = d
    return _vapid


def _genereaza_vapid():
    try:
        from cryptography.hazmat.primitives import serialization
        from py_vapid import Vapid02
        v = Vapid02()
        v.generate_keys()
        raw = v.public_key.public_bytes(serialization.Encoding.X962,
                                        serialization.PublicFormat.UncompressedPoint)
        return {"private": v.private_pem().decode(),
                "public": base64.urlsafe_b64encode(raw).decode().rstrip("=")}
    except Exception as e:
        logare.info(f"[push] chei VAPID: {str(e)[:80]}")
        return None


def cheie_publica():
    """Cheia publica (base64url) pe care o primeste browserul la abonare; None daca
    pywebpush/cryptography lipsesc."""
    v = _incarca_vapid()
    return v["public"] if v else None


# ---- abonamentele ----
def _c():
    global _gata
    c = jurnal.conexiune()
    if _gata is not c:
        with jurnal.lock:
            c.execute("""CREATE TABLE IF NOT EXISTS push_abonamente(
                id INTEGER PRIMARY KEY, user TEXT NOT NULL, endpoint TEXT NOT NULL UNIQUE,
                p256dh TEXT NOT NULL, auth TEXT NOT NULL, nivel TEXT NOT NULL DEFAULT 'critic',
                agent TEXT, creat REAL, ultima REAL, erori INTEGER NOT NULL DEFAULT 0)""")
            c.execute("CREATE INDEX IF NOT EXISTS push_user ON push_abonamente(user)")
        _gata = c
    return c


def _rand(r):
    return {"id": r[0], "user": r[1], "endpoint": r[2], "nivel": r[3], "agent": r[4],
            "creat": r[5], "ultima": r[6], "erori": r[7]}


def aboneaza(user, abonament, nivel="critic", agent=""):
    """Salveaza (sau reinnoieste) abonamentul unui browser. Acelasi endpoint trece la
    utilizatorul curent (telefonul a schimbat contul)."""
    if not isinstance(abonament, dict) or not abonament.get("endpoint"):
        raise ValueError(t("abonament invalid"))
    keys = abonament.get("keys") or {}
    if not keys.get("p256dh") or not keys.get("auth"):
        raise ValueError(t("abonamentului îi lipsesc cheile"))
    ep = str(abonament["endpoint"])[:2000]
    if not ep.startswith("https://"):
        raise ValueError(t("endpoint-ul trebuie sa fie https"))
    nivel = nivel if nivel in NIVELURI else "critic"
    c = _c()
    with jurnal.lock:
        c.execute("""INSERT INTO push_abonamente(user, endpoint, p256dh, auth, nivel, agent, creat)
                     VALUES(?,?,?,?,?,?,?)
                     ON CONFLICT(endpoint) DO UPDATE SET user=excluded.user,
                       p256dh=excluded.p256dh, auth=excluded.auth, nivel=excluded.nivel,
                       agent=excluded.agent, erori=0""",
                  (user, ep, str(keys["p256dh"])[:200], str(keys["auth"])[:100], nivel,
                   (agent or "")[:200], time.time()))
        r = c.execute("SELECT id, user, endpoint, nivel, agent, creat, ultima, erori "
                      "FROM push_abonamente WHERE endpoint=?", (ep,)).fetchone()
    return _rand(r)


def dezaboneaza(user, endpoint, admin=False):
    """Sterge abonamentul; un utilizator doar pe ale lui (adminul pe oricare)."""
    c = _c()
    with jurnal.lock:
        if admin:
            cur = c.execute("DELETE FROM push_abonamente WHERE endpoint=?", (endpoint,))
        else:
            cur = c.execute("DELETE FROM push_abonamente WHERE endpoint=? AND user=?",
                            (endpoint, user))
    return cur.rowcount > 0


def lista(user=None):
    c = _c()
    with jurnal.lock:
        if user is None:
            rows = c.execute("SELECT id, user, endpoint, nivel, agent, creat, ultima, erori "
                             "FROM push_abonamente ORDER BY creat").fetchall()
        else:
            rows = c.execute("SELECT id, user, endpoint, nivel, agent, creat, ultima, erori "
                             "FROM push_abonamente WHERE user=? ORDER BY creat", (user,)).fetchall()
    return [_rand(r) for r in rows]


def _toate():
    c = _c()
    with jurnal.lock:
        return c.execute("SELECT id, user, endpoint, nivel, p256dh, auth FROM push_abonamente").fetchall()


# ---- trimiterea ----
def _trimite_unul(rid, endpoint, p256dh, auth, payload, ttl):
    """True daca a plecat; sterge abonamentul daca serviciul spune ca nu mai exista."""
    v = _incarca_vapid()
    if not v:
        return False
    fn = _webpush
    if fn is None:
        from pywebpush import webpush
        fn = webpush
    try:
        fn(subscription_info={"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
           data=json.dumps(payload, ensure_ascii=False), vapid_private_key=v["private"],
           vapid_claims={"sub": "mailto:nvr@localhost"}, ttl=ttl, timeout=10)
        c = _c()
        with jurnal.lock:
            c.execute("UPDATE push_abonamente SET ultima=?, erori=0 WHERE id=?", (time.time(), rid))
        return True
    except Exception as e:
        cod = getattr(getattr(e, "response", None), "status_code", None)
        c = _c()
        with jurnal.lock:
            if cod in (404, 410):          # abonament revocat de browser
                c.execute("DELETE FROM push_abonamente WHERE id=?", (rid,))
            else:
                c.execute("UPDATE push_abonamente SET erori=erori+1 WHERE id=?", (rid,))
        logare.info(f"[push] {endpoint[:40]}…: {cod or str(e)[:80]}")
        return False


def _vizibil(user, cam_id):
    if cam_id is None:
        return True
    u = users.get(user)
    if not u:
        return False
    try:
        vis = users.allowed_ids(u, camere_fn() or [])
    except Exception:
        vis = None
    return vis is None or cam_id in vis


def _nume_cam(cam_id):
    for c in camere_fn() or []:
        if c.get("id") == cam_id:
            return c.get("name") or f"cam {cam_id}"
    return f"cam {cam_id}"


def trimite(payload, user=None, sev=None, cam_id=None, ttl=TTL_EVENIMENT, sync=False):
    """Livreaza un mesaj abonatilor potriviti: ai unui utilizator (user=) sau tuturor celor
    cu nivelul <= sev si cu acces la camera. Intoarce cate trimiteri s-au incercat."""
    tinte = []
    for rid, u, ep, nivel, p256dh, auth in _toate():
        if user is not None and u != user:
            continue
        if sev is not None and _SEV_RANK.get(sev, 0) < _SEV_RANK.get(nivel, 2):
            continue
        if cam_id is not None and not _vizibil(u, cam_id):
            continue
        tinte.append((rid, ep, p256dh, auth))
    if not tinte:
        return 0

    def work():
        for rid, ep, p256dh, auth in tinte:
            _trimite_unul(rid, ep, p256dh, auth, payload, ttl)
    if sync:
        work()
    else:
        threading.Thread(target=work, daemon=True, name="push").start()
    return len(tinte)


def eveniment(e, sync=False):
    """Chemata din rules._journal pentru orice eveniment jurnalizat: cele cu severitate
    >= atentie ajung la abonatii cu nivelul potrivit, cu legatura direct in redare."""
    sev = e.get("sev") or "info"
    if _SEV_RANK.get(sev, 0) < 1:
        return 0
    cam = e.get("cam")
    det = (e.get("text") or e.get("nume") or e.get("numar") or e.get("eticheta")
           or e.get("comportament") or e.get("subtip") or "")
    tip = t(TIP_RO.get(e.get("tip"), e.get("tip") or "eveniment"))
    titlu = ("🚨 " if sev == "critic" else "⚠️ ") + (_nume_cam(cam) if cam is not None else "NVR")
    corp = f"{tip}{': ' + str(det)[:80] if det else ''}"
    ts = e.get("t") or ""
    if cam is not None and len(ts) >= 19:
        h, m, s = int(ts[11:13]), int(ts[14:16]), int(ts[17:19])
        url = f"/#pb/{cam}/{ts[:10]}/{max(0, h * 3600 + m * 60 + s - 10)}"
    else:
        url = "/#ev/inc"
    return trimite({"titlu": titlu, "corp": corp, "url": url, "tag": e.get("id") or "nvr",
                    "sev": sev, "t": ts}, sev=sev, cam_id=cam, sync=sync)


def test(user):
    """Mesaj de proba pe toate dispozitivele utilizatorului (sincron, pentru buton)."""
    return trimite({"titlu": f"✅ {C.NUME}", "corp": t("Notificarile push functioneaza pe acest dispozitiv."), "url": "/#ev/inc", "tag": "test"}, user=user, ttl=60, sync=True)


def reset():
    """Pentru teste: uita conexiunea si cheile."""
    global _gata, _vapid
    _gata = None
    _vapid = None
