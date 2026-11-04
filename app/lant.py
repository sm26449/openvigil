#!/usr/bin/env python3
"""Lantul de custodie al inregistrarilor: SHA-256 la inchiderea fiecarui segment.

Serviciul de inregistrare hash-uieste fiecare segment imediat ce ffmpeg l-a inchis (un minut
dupa ce a inceput urmatorul) si tine amprenta in PostgreSQL (tabelul `segmente`):
(camera, nume, octeti, sha256, cand). Fisa probatorie listeaza segmentele-sursa cu hash-urile
lor de la inchidere, iar `verifica()` recalculeaza oricand si spune daca fisierul mai e cel de
atunci. Mutarea in arhiva (record.py) se verifica tot prin hash: copia trebuie sa aiba aceeasi
amprenta.

Din 2.0.0 baza e PostgreSQL (app/db.py): scriitorul e nvr-record, cititorul nvr-web — doua
procese care impart aceeasi baza (mai curat decat WAL-ul SQLite din doua procese).
"""
import hashlib
import os
import threading
import time

import db
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARGINE = 60            # secunde dupa sfarsitul teoretic al segmentului pana il consideram inchis
PE_TUR = 30             # cate segmente hash-uim intr-un tur (un tur pe minut; ~150 MB fiecare —
                        # mai mult ar concura cu inregistrarea pe disc)
INAPOI = 6 * 3600       # restanta: doar segmentele din ultimele 6 ore (cele mai noi primele);
                        # un istoric de 14 zile x 18 camere ar insemna ~10 TB de citit degeaba

SCHEMA = """
CREATE TABLE IF NOT EXISTS segmente (
    cam      INTEGER NOT NULL,
    nume     TEXT    NOT NULL,
    epoch    BIGINT  NOT NULL,
    cale     TEXT    NOT NULL,
    octeti   BIGINT  NOT NULL,
    sha256   TEXT    NOT NULL,
    calculat DOUBLE PRECISION NOT NULL,
    arhivat  DOUBLE PRECISION,
    sha256_orig TEXT,              -- amprenta ORIGINALULUI inainte de o eventuala re-encodare
    recodat  DOUBLE PRECISION,     -- momentul re-encodarii (reencode.py); NULL = material brut
    PRIMARY KEY (cam, nume)
);
CREATE INDEX IF NOT EXISTS seg_cam_epoch ON segmente(cam, epoch);
"""
COLS = "cam, nume, epoch, cale, octeti, sha256, calculat, arhivat, sha256_orig, recodat"
MIGRARI = {
    # 2.0.3: lantul retine derivarea la re-encodare (sha256_orig + recodat) ca verifica() sa
    # ramana valabila dupa recomprimare, pastrand totusi amprenta originalului
    2: "ALTER TABLE segmente ADD COLUMN IF NOT EXISTS sha256_orig TEXT; "
       "ALTER TABLE segmente ADD COLUMN IF NOT EXISTS recodat DOUBLE PRECISION",
}
_gata = False


def _init():
    global _gata
    if not _gata:
        db.aplica("lant", SCHEMA, MIGRARI)
        _gata = True


def reset():
    """Pentru teste: uita ca schema a fost verificata."""
    global _gata
    _gata = False


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
    _init()
    with db.conn() as c:
        c.execute("INSERT INTO segmente (cam, nume, epoch, cale, octeti, sha256, calculat, arhivat) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,NULL) "
                  "ON CONFLICT (cam, nume) DO UPDATE SET epoch=EXCLUDED.epoch, cale=EXCLUDED.cale, "
                  "octeti=EXCLUDED.octeti, sha256=EXCLUDED.sha256, calculat=EXCLUDED.calculat, "
                  "arhivat=EXCLUDED.arhivat",
                  (cam, nume, int(epoch), _rel(cale), octeti, sha, time.time()))
    return {"cam": cam, "nume": nume, "epoch": int(epoch), "cale": os.path.abspath(cale),
            "octeti": octeti, "sha256": sha}


def _rand(r):
    return {"cam": r[0], "nume": r[1], "epoch": r[2], "cale": _abs(r[3]), "octeti": r[4],
            "sha256": r[5], "calculat": r[6], "arhivat": r[7],
            "sha256_orig": r[8], "recodat": r[9]}


def get(cam, nume):
    _init()
    with db.conn() as c:
        r = c.execute(f"SELECT {COLS} FROM segmente WHERE cam=%s AND nume=%s", (cam, nume)).fetchone()
    return _rand(r) if r else None


def cauta_hash(sha256):
    """Segmentul cu amprenta data (verificarea publica a probelor), sau None."""
    _init()
    with db.conn() as c:
        r = c.execute(f"SELECT {COLS} FROM segmente WHERE sha256 = %s ORDER BY calculat DESC LIMIT 1",
                      (sha256,)).fetchone()
    return _rand(r) if r else None


def lista(cam, since=None, until=None):
    """Segmentele unei camere (cu hash) in [since, until) epoch, in ordine."""
    q, p = f"SELECT {COLS} FROM segmente WHERE cam=%s", [cam]
    if since is not None:
        q += " AND epoch + 300 > %s"
        p.append(int(since))
    if until is not None:
        q += " AND epoch < %s"
        p.append(int(until))
    _init()
    with db.conn() as c:
        rows = c.execute(q + " ORDER BY epoch", p).fetchall()
    return [_rand(r) for r in rows]


def muta(cam, nume, cale_noua, arhivat=True):
    """Dupa mutarea in arhiva: calea noua + momentul."""
    _init()
    with db.conn() as c:
        c.execute("UPDATE segmente SET cale=%s, arhivat=%s WHERE cam=%s AND nume=%s",
                  (_rel(cale_noua), time.time() if arhivat else None, cam, nume))


def recodat(cam, nume, cale_noua):
    """Dupa o RE-ENCODARE (reencode.py): fisierul si-a schimbat continutul (alt codec) si s-a
    mutat in arhiva. Recalculam hash-ul nou si PASTRAM amprenta originalului (prima data) in
    sha256_orig + momentul recodarii, ca lantul sa ramana onest: verifica() merge pe continutul
    nou, iar fisa poate arata ca segmentul a fost recomprimat (derivare, nu material brut).
    Daca segmentul n-are amprenta in lant, nu facem nimic (intoarce None)."""
    r = get(cam, nume)
    if not r:
        return None
    sha_nou = hash_fisier(cale_noua)
    octeti = os.path.getsize(cale_noua)
    orig = r.get("sha256_orig") or r["sha256"]     # pastram PRIMUL original, nu-l rescriem
    with db.conn() as c:
        c.execute("UPDATE segmente SET cale=%s, octeti=%s, sha256=%s, sha256_orig=%s, "
                  "recodat=%s, arhivat=%s WHERE cam=%s AND nume=%s",
                  (_rel(cale_noua), octeti, sha_nou, orig, time.time(), time.time(), cam, nume))
    return {"sha256": sha_nou, "sha256_orig": orig}


def sterge(cam, nume):
    _init()
    with db.conn() as c:
        c.execute("DELETE FROM segmente WHERE cam=%s AND nume=%s", (cam, nume))


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
            "sha256_orig": r.get("sha256_orig"), "recodat": r.get("recodat"),
            "motiv": "" if acum == r["sha256"] else "CONTINUT MODIFICAT dupa inchidere"}


def tur(files_fn, cams, segment=300, now=None, limita=PE_TUR, inapoi=INAPOI):
    """Un tur: hash-uieste segmentele inchise (epoch + segment + MARGINE < now) care nu au
    inca amprenta, cele mai NOI primele (lantul e util imediat pentru ce se intampla acum),
    doar din ultimele `inapoi` secunde. files_fn(cam) -> [(epoch, cale, nume)]."""
    now = now or time.time()
    _init()
    facute = 0
    for cam in cams:
        with db.conn() as c:
            stiute = {r[0] for r in c.execute("SELECT nume FROM segmente WHERE cam=%s", (cam,)).fetchall()}
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
    _init()
    n = 0
    for cam in cams:
        exista = {nume for _, _, nume in files_fn(cam)}
        with db.conn() as c:
            rows = c.execute("SELECT nume FROM segmente WHERE cam=%s", (cam,)).fetchall()
            for (nume,) in rows:
                if nume not in exista:
                    c.execute("DELETE FROM segmente WHERE cam=%s AND nume=%s", (cam, nume))
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
