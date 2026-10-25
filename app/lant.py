#!/usr/bin/env python3
"""Lantul de custodie al inregistrarilor: SHA-256 la inchiderea fiecarui segment.

Pana acum fisa probatorie calcula hash-ul pe o regenerare a clipului; daca segmentele de
pe disc erau atinse intre timp, nimeni n-ar fi stiut. Acum serviciul de inregistrare
hash-uieste fiecare segment imediat ce ffmpeg l-a inchis (un minut dupa ce a inceput
urmatorul) si tine amprenta in data/segmente.db: (camera, nume, octeti, sha256, cand).
Fisa probatorie listeaza segmentele-sursa cu hash-urile lor de la inchidere, iar
`verifica()` recalculeaza oricand si spune daca fisierul mai e cel de atunci. Mutarea in
arhiva (record.py) se verifica tot prin hash: copia trebuie sa aiba aceeasi amprenta.

Baza e proprie (nu events.db): scriitorul e nvr-record, cititorul nvr-web — doua procese,
iar WAL + busy_timeout le impaca; schema se creeaza de oricine deschide primul.
"""
import hashlib
import os
import sqlite3
import threading
import time
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(ROOT, "data", "segmente.db")
MARGINE = 60            # secunde dupa sfarsitul teoretic al segmentului pana il consideram inchis
PE_TUR = 30             # cate segmente hash-uim intr-un tur (un tur pe minut; ~150 MB fiecare —
                        # mai mult ar concura cu inregistrarea pe disc)
INAPOI = 6 * 3600       # restanta: doar segmentele din ultimele 6 ore (cele mai noi primele);
                        # un istoric de 14 zile x 18 camere ar insemna ~10 TB de citit degeaba
_lock = threading.RLock()
_conn = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS segmente (
    cam      INTEGER NOT NULL,
    nume     TEXT    NOT NULL,
    epoch    INTEGER NOT NULL,
    cale     TEXT    NOT NULL,
    octeti   INTEGER NOT NULL,
    sha256   TEXT    NOT NULL,
    calculat REAL    NOT NULL,
    arhivat  REAL,
    PRIMARY KEY (cam, nume)
);
CREATE INDEX IF NOT EXISTS seg_cam_epoch ON segmente(cam, epoch);
"""


def _c():
    global _conn
    with _lock:
        if _conn is None:
            os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
            c = sqlite3.connect(DB_FILE, check_same_thread=False, isolation_level=None,
                                timeout=15)
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.executescript(SCHEMA)
            # caile vechi, absolute (ale altei instalari sau ale gazdei, cand rulam in container),
            # devin relative la radacina proiectului: data/recordings/camN/nume.mp4
            c.execute("UPDATE segmente SET cale = substr(cale, instr(cale, '/data/recordings/') + 1) "
                      "WHERE cale LIKE '/%' AND instr(cale, '/data/recordings/') > 0")
            for suf in ("", "-wal", "-shm"):
                try:
                    os.chmod(DB_FILE + suf, 0o600)
                except OSError:
                    pass
            _conn = c
        return _conn


def reset():
    """Pentru teste."""
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
        _conn = None


def hash_fisier(cale):
    h = hashlib.sha256()
    with open(cale, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(cale):
    """Pe disc memoram calea relativa la radacina proiectului cand fisierul e sub ea, ca lantul
    sa ramana valabil daca instalarea se muta (alt director, container). Arhiva din afara
    radacinii ramane absoluta."""
    cale = os.path.abspath(cale)
    if cale.startswith(ROOT + os.sep):
        return os.path.relpath(cale, ROOT)
    return cale


def _abs(cale):
    if os.path.isabs(cale):
        if os.path.exists(cale):
            return cale
        i = cale.find("/data/recordings/")           # cale absoluta a altei instalari
        return os.path.join(ROOT, cale[i + 1:]) if i >= 0 else cale
    return os.path.join(ROOT, cale)


def inregistreaza(cam, epoch, cale):
    """Hash-uieste un segment inchis si il memoreaza. Intoarce randul."""
    sha = hash_fisier(cale)
    octeti = os.path.getsize(cale)
    nume = os.path.basename(cale)
    with _lock:
        _c().execute("INSERT OR REPLACE INTO segmente(cam, nume, epoch, cale, octeti, sha256, calculat, arhivat) "
                     "VALUES(?,?,?,?,?,?,?,NULL)", (cam, nume, int(epoch), _rel(cale), octeti, sha, time.time()))
    return {"cam": cam, "nume": nume, "epoch": int(epoch), "cale": os.path.abspath(cale), "octeti": octeti, "sha256": sha}


def _rand(r):
    return {"cam": r[0], "nume": r[1], "epoch": r[2], "cale": _abs(r[3]), "octeti": r[4],
            "sha256": r[5], "calculat": r[6], "arhivat": r[7]}


def get(cam, nume):
    with _lock:
        r = _c().execute("SELECT * FROM segmente WHERE cam=? AND nume=?", (cam, nume)).fetchone()
    return _rand(r) if r else None


def cauta_hash(sha256):
    """Segmentul cu amprenta data (verificarea publica a probelor), sau None."""
    r = _c().execute("SELECT * FROM segmente WHERE sha256 = ? ORDER BY calculat DESC LIMIT 1",
                     (sha256,)).fetchone()
    return _rand(r) if r else None


def lista(cam, since=None, until=None):
    """Segmentele unei camere (cu hash) in [since, until) epoch, in ordine."""
    q, p = "SELECT * FROM segmente WHERE cam=?", [cam]
    if since is not None:
        q += " AND epoch + 300 > ?"
        p.append(int(since))
    if until is not None:
        q += " AND epoch < ?"
        p.append(int(until))
    with _lock:
        rows = _c().execute(q + " ORDER BY epoch", p).fetchall()
    return [_rand(r) for r in rows]


def muta(cam, nume, cale_noua, arhivat=True):
    """Dupa mutarea in arhiva: calea noua + momentul."""
    with _lock:
        _c().execute("UPDATE segmente SET cale=?, arhivat=? WHERE cam=? AND nume=?",
                     (_rel(cale_noua), time.time() if arhivat else None, cam, nume))


def sterge(cam, nume):
    with _lock:
        _c().execute("DELETE FROM segmente WHERE cam=? AND nume=?", (cam, nume))


def verifica(cam, nume):
    """Recalculeaza hash-ul fisierului si il compara cu cel de la inchidere.
    Intoarce {ok, sha256_acum, sha256_initial, motiv}."""
    r = get(cam, nume)
    if not r:
        return {"ok": None, "motiv": "segment fara amprenta (inregistrat inainte de lant sau inca deschis)"}
    if not os.path.exists(r["cale"]):
        return {"ok": False, "motiv": "fisierul lipseste", "sha256_initial": r["sha256"]}
    acum = hash_fisier(r["cale"])
    return {"ok": acum == r["sha256"], "sha256_acum": acum, "sha256_initial": r["sha256"],
            "motiv": "" if acum == r["sha256"] else "CONTINUT MODIFICAT dupa inchidere"}


def tur(files_fn, cams, segment=300, now=None, limita=PE_TUR, inapoi=INAPOI):
    """Un tur: hash-uieste segmentele inchise (epoch + segment + MARGINE < now) care nu au
    inca amprenta, cele mai NOI primele (lantul e util imediat pentru ce se intampla acum),
    doar din ultimele `inapoi` secunde. files_fn(cam) -> [(epoch, cale, nume)]."""
    now = now or time.time()
    facute = 0
    for cam in cams:
        with _lock:
            stiute = {r[0] for r in _c().execute("SELECT nume FROM segmente WHERE cam=?", (cam,))}
        for epoch, cale, nume in sorted(files_fn(cam), reverse=True):
            if nume in stiute or epoch + segment + MARGINE > now or (inapoi and epoch < now - inapoi):
                continue
            try:
                inregistreaza(cam, epoch, cale)
                facute += 1
            except OSError as e:
                logare.info(f"[lant] cam{cam} {nume}: {str(e)[:60]}")
            if facute >= limita:
                return facute
    return facute


def curata(files_fn, cams):
    """Uita amprentele fisierelor care nu mai exista (sterse de retentie)."""
    n = 0
    for cam in cams:
        exista = {nume for _, _, nume in files_fn(cam)}
        with _lock:
            rows = _c().execute("SELECT nume FROM segmente WHERE cam=?", (cam,)).fetchall()
            for (nume,) in rows:
                if nume not in exista:
                    _c().execute("DELETE FROM segmente WHERE cam=? AND nume=?", (cam, nume))
                    n += 1
    return n


def start(files_fn, cams_fn, segment=300):
    """Firul din nvr-record: un tur pe minut, curatenie o data pe ora."""
    def bucla():
        ultima_curatare = 0.0
        while True:
            try:
                cams = cams_fn()
                n = tur(files_fn, cams, segment)
                if n:
                    logare.info(f"[lant] {n} segmente hash-uite")
                if time.time() - ultima_curatare > 3600:
                    curata(files_fn, cams)
                    ultima_curatare = time.time()
            except Exception as e:
                logare.info(f"[lant] tur: {str(e)[:80]}")
            time.sleep(60)
    threading.Thread(target=bucla, daemon=True, name="lant").start()
