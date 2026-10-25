#!/usr/bin/env python3
"""Coada de joburi: lucrul lung (export MP4, fisa probatorie, clip, chenare, dosar ZIP,
adaugarea unei camere) ruleaza pe fire de lucru proprii, nu in firele de cerere.

De ce: waitress are 16 fire. Un export de 30 de minute de pe NVR tine un fir ~30 de
minute, chenarele pe clip pana la 45 s + YOLO pe CPU, adaugarea unei camere ~40 s. Cateva
exporturi plus o camera moarta si interfata ingheta pentru toti. Acum ruta raspunde
imediat cu 202 + jobul, interfata intreaba /api/jobs/<id> si descarca fisierul la final.

Jobul e un rand in data/events.db (tabelul `jobs`, conexiunea din jurnal.py): tip, stare
(asteapta / ruleaza / gata / eroare / anulat), progres 0-100, mesaj, parametri, rezultat,
fisierul produs (data/tmp/joburi/, sters dupa FILE_TTL). Un job identic (acelasi tip +
parametri) aflat deja in coada se intoarce in loc sa se dubleze — dublu-click-ul pe
„Descarca” nu porneste doi ffmpeg. Parolele (camera noua) NU se persista: merg prin
`secret`, doar in memorie.

Handler-ele se inregistreaza din modulele care detin logica (routes_media, routes_cameras,
routes_incidents): `jobs.register(tip, fn)`, cu `fn(job) -> dict`. Progresul se raporteaza
prin `job.progres(p, mesaj)`, fisierul prin `job.fisier(cale, nume, mime)`, erorile pentru
utilizator prin `raise JobError("...")`. `job.anulat` + `job.proc` (ffmpeg-ul curent) fac
anularea posibila si pentru un job care deja ruleaza.
"""
import json
import os
import queue
import threading
import time
import traceback
import uuid

import jurnal
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(ROOT, "data", "tmp", "joburi")
FILE_TTL = 3600          # fisierele produse se sterg dupa o ora
ROW_TTL = 7 * 86400      # istoricul joburilor se tine o saptamana
WORKERS = 3              # cate joburi ruleaza simultan (ffmpeg + NVENC: 3 incap lejer)
FINALE = ("gata", "eroare", "anulat")

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT    PRIMARY KEY,
    tip         TEXT    NOT NULL,
    stare       TEXT    NOT NULL DEFAULT 'asteapta',
    progres     INTEGER NOT NULL DEFAULT 0,
    mesaj       TEXT    NOT NULL DEFAULT '',
    parametri   TEXT    NOT NULL DEFAULT '{}',
    rezultat    TEXT,
    fisier      TEXT,
    nume_fisier TEXT,
    mime        TEXT,
    creat_de    TEXT,
    cam         INTEGER,
    creat       INTEGER NOT NULL,
    pornit      INTEGER,
    terminat    INTEGER
);
CREATE INDEX IF NOT EXISTS jobs_creat ON jobs(creat);
CREATE INDEX IF NOT EXISTS jobs_stare ON jobs(stare);
"""
COLS = ("id", "tip", "stare", "progres", "mesaj", "parametri", "rezultat", "fisier",
        "nume_fisier", "mime", "creat_de", "cam", "creat", "pornit", "terminat")

_handlers = {}
_q = queue.Queue()
_active = {}             # id -> Job (ruleaza acum; pentru anulare)
_threads = []
_stop = threading.Event()
_gata = None


class JobError(Exception):
    """Eroare de aratat utilizatorului (nu un bug): 'nu exista inregistrari in interval'."""


def register(tip, fn):
    _handlers[tip] = fn


def _db():
    global _gata
    c = jurnal.conexiune()
    if _gata is not c:
        with jurnal.lock:
            c.executescript(SCHEMA)
        _gata = c
    return c


def _dict(row):
    d = dict(zip(COLS, row))
    for k in ("parametri", "rezultat"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except ValueError:
            d[k] = None
    for k in ("creat", "pornit", "terminat"):
        d[k + "_t"] = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(d[k]))
                       if d[k] else None)
    d["are_fisier"] = bool(d["fisier"] and os.path.exists(d["fisier"]))
    d.pop("fisier")          # calea de pe disc nu iese din server
    return d


def _set(jid, **campuri):
    c = _db()
    sets = ", ".join(f"{k} = ?" for k in campuri)
    with jurnal.lock:
        c.execute(f"UPDATE jobs SET {sets} WHERE id = ?", (*campuri.values(), jid))


class Job:
    def __init__(self, jid, tip, parametri, creat_de, secret=None):
        self.id, self.tip, self.parametri = jid, tip, parametri
        self.creat_de = creat_de
        self.secret = secret or {}
        self.anulat = False
        self.proc = None          # subprocesul curent (ffmpeg) — omorat la anulare
        self._ultim = 0.0
        self._p = 0
        self.mesaj = ""

    def progres(self, p, mesaj=None):
        """0..100 + mesaj scurt; scris in baza cel mult de doua ori pe secunda."""
        p = max(0, min(100, int(p)))
        if mesaj is not None:
            self.mesaj = str(mesaj)[:200]
        acum = time.time()
        if p == self._p and mesaj is None:
            return
        if acum - self._ultim < 0.5 and p < 100 and mesaj is None:
            return
        self._p, self._ultim = p, acum
        _set(self.id, progres=p, mesaj=self.mesaj)

    def fisier(self, cale, nume, mime="application/octet-stream"):
        """Fisierul produs, de descarcat prin /api/jobs/<id>/fisier (sters dupa FILE_TTL)."""
        _set(self.id, fisier=cale, nume_fisier=nume, mime=mime)

    def cale(self, ext):
        """O cale libera in directorul joburilor, pentru fisierul produs."""
        os.makedirs(DIR, exist_ok=True)
        return os.path.join(DIR, f"{self.id}.{ext}")


def submit(tip, parametri=None, who=None, cam=None, secret=None):
    """Pune un job in coada (sau intoarce cel identic aflat deja in asteptare/rulare).
    Intoarce randul jobului (dict), cu stare 'asteapta'."""
    if tip not in _handlers:
        raise ValueError(f"tip de job necunoscut: {tip}")
    parametri = parametri or {}
    pj = json.dumps(parametri, sort_keys=True, ensure_ascii=False)
    c = _db()
    with jurnal.lock:
        row = c.execute(f"SELECT {','.join(COLS)} FROM jobs WHERE tip = ? AND parametri = ? "
                        "AND stare IN ('asteapta', 'ruleaza') LIMIT 1", (tip, pj)).fetchone()
        if row:
            return _dict(row)
        jid = uuid.uuid4().hex[:12]
        c.execute("INSERT INTO jobs (id, tip, parametri, creat_de, cam, creat) "
                  "VALUES (?,?,?,?,?,?)", (jid, tip, pj, who, cam, int(time.time())))
    _q.put(Job(jid, tip, parametri, who, secret))
    return get(jid)


def get(jid):
    c = _db()
    with jurnal.lock:
        row = c.execute(f"SELECT {','.join(COLS)} FROM jobs WHERE id = ?", (jid,)).fetchone()
    return _dict(row) if row else None


def lista(who=None, limit=50, doar_active=False):
    """Cele mai noi joburi (ale unui utilizator, sau toate cand who=None)."""
    cond, args = [], []
    if who is not None:
        cond.append("creat_de = ?")
        args.append(who)
    if doar_active:
        cond.append("stare IN ('asteapta', 'ruleaza')")
    sql = f"SELECT {','.join(COLS)} FROM jobs"
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY creat DESC, rowid DESC LIMIT ?"
    args.append(max(1, int(limit)))
    c = _db()
    with jurnal.lock:
        rows = c.execute(sql, args).fetchall()
    return [_dict(r) for r in rows]


def cale_fisier(jid):
    """(cale, nume, mime) ale fisierului produs, sau None."""
    c = _db()
    with jurnal.lock:
        row = c.execute("SELECT fisier, nume_fisier, mime FROM jobs WHERE id = ? "
                        "AND stare = 'gata'", (jid,)).fetchone()
    if not row or not row[0] or not os.path.exists(row[0]):
        return None
    return row


def cancel(jid):
    """Anuleaza: un job in asteptare nu mai porneste; unul care ruleaza e oprit (ffmpeg-ul
    lui primeste kill). Intoarce False daca jobul nu exista sau s-a terminat deja."""
    j = get(jid)
    if not j or j["stare"] in FINALE:
        return False
    job = _active.get(jid)
    if job is not None:
        job.anulat = True
        if job.proc is not None:
            try:
                job.proc.kill()
            except OSError:
                pass
    else:
        _set(jid, stare="anulat", terminat=int(time.time()), mesaj="anulat")
    return True


def _ruleaza(job):
    _set(job.id, stare="ruleaza", pornit=int(time.time()), mesaj="")
    _active[job.id] = job
    try:
        rez = _handlers[job.tip](job)
        if job.anulat:
            raise JobError("anulat")
        _set(job.id, stare="gata", progres=100, terminat=int(time.time()),
             rezultat=json.dumps(rez or {}, ensure_ascii=False), mesaj=job.mesaj)
    except JobError as e:
        stare = "anulat" if job.anulat else "eroare"
        _set(job.id, stare=stare, terminat=int(time.time()), mesaj=str(e)[:200])
    except Exception as e:                      # bug in handler: nu doboara lucratorul
        traceback.print_exc()
        _set(job.id, stare="anulat" if job.anulat else "eroare", terminat=int(time.time()),
             mesaj=f"eroare interna: {str(e)[:160]}")
    finally:
        _active.pop(job.id, None)


def _lucrator():
    while not _stop.is_set():
        try:
            job = _q.get(timeout=0.5)
        except queue.Empty:
            continue
        j = get(job.id)
        if j and j["stare"] == "asteapta":   # anulat intre timp -> nu mai ruleaza
            _ruleaza(job)
        _q.task_done()


def curata(acum=None):
    """Sterge fisierele joburilor mai vechi de FILE_TTL si randurile mai vechi de ROW_TTL.
    Intoarce (fisiere_sterse, randuri_sterse)."""
    acum = acum or time.time()
    c = _db()
    fisiere = 0
    with jurnal.lock:
        rows = c.execute("SELECT id, fisier FROM jobs WHERE fisier IS NOT NULL "
                         "AND terminat IS NOT NULL AND terminat < ?",
                         (int(acum - FILE_TTL),)).fetchall()
        for jid, f in rows:
            try:
                os.remove(f)
                fisiere += 1
            except OSError:
                pass
            c.execute("UPDATE jobs SET fisier = NULL WHERE id = ?", (jid,))
        randuri = c.execute("DELETE FROM jobs WHERE creat < ? AND stare IN ('gata','eroare','anulat')",
                            (int(acum - ROW_TTL),)).rowcount
    # orfane (job sters, fisier ramas)
    try:
        for fn in os.listdir(DIR):
            p = os.path.join(DIR, fn)
            if acum - os.path.getmtime(p) > FILE_TTL:
                os.remove(p)
                fisiere += 1
    except OSError:
        pass
    return fisiere, randuri


def _curatator():
    while not _stop.wait(600):
        try:
            curata()
        except Exception as e:
            logare.info(f"[jobs] curatare: {str(e)[:80]}")


def start(workers=WORKERS):
    """Porneste lucratorii. Joburile ramase 'asteapta/ruleaza' de la procesul anterior
    nu mai au cine sa le faca: devin eroare, ca interfata sa nu astepte la nesfarsit."""
    global _threads
    if _threads:
        return
    _stop.clear()
    c = _db()
    with jurnal.lock:
        c.execute("UPDATE jobs SET stare = 'eroare', terminat = ?, "
                  "mesaj = 'serviciul a repornit inainte sa termine' "
                  "WHERE stare IN ('asteapta', 'ruleaza')", (int(time.time()),))
    os.makedirs(DIR, exist_ok=True)
    for i in range(max(1, workers)):
        t = threading.Thread(target=_lucrator, name=f"job-{i}", daemon=True)
        t.start()
        _threads.append(t)
    t = threading.Thread(target=_curatator, name="job-curatare", daemon=True)
    t.start()
    _threads.append(t)


def stop():
    """Opreste lucratorii (teste). Joburile aflate in coada raman 'asteapta'."""
    global _threads
    _stop.set()
    for t in _threads:
        t.join(timeout=5)
    _threads = []
    while not _q.empty():
        try:
            _q.get_nowait()
        except queue.Empty:
            break


def asteapta(jid, timeout=30):
    """Blocheaza pana jobul ajunge intr-o stare finala (teste + scripturi)."""
    limita = time.time() + timeout
    while time.time() < limita:
        j = get(jid)
        if not j or j["stare"] in FINALE:
            return j
        time.sleep(0.05)
    return get(jid)
