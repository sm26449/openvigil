#!/usr/bin/env python3
"""Jurnalul permanent de evenimente — SQLite in mod WAL (data/events.db).

Pana pe 2026-10-23 jurnalul era un JSONL (events.jsonl) citit de la coada, plafonat la
64MB (~2 luni). Orice intrebare "ce s-a intamplat in ziua X" citea tot fisierul
(rezumatul zilnic: 3 s pe 42MB), iar retentia era pe octeti, nu pe timp. Aici fiecare
eveniment e un rand cu timp UTC (epoch), camera, tip, severitate si restul campurilor
ca JSON; indecsii pe (ts), (cam, ts), (tip, ts), (sev, ts) fac orice filtru al lui
`query()` o citire de cateva ms, indiferent cat istoric tinem.

Contractul cu restul sistemului NU se schimba: `rules._journal()` scrie, `rules.journal()`
citeste, si ambele intorc/primesc aceleasi dictionare plate ca inainte (id, t, tip, sev,
cam, + campurile evenimentului). Un singur writer (nvr-web); nvr-ai doar citeste (ask,
episoade) — WAL permite cititori din alt proces fara sa blocheze scrierea. In fiecare
proces: o singura conexiune, sub lock; SQLite isi face treaba cu fsync-ul
(synchronous=NORMAL + WAL: un crash pierde cel mult ultimele ms, niciodata baza).

Migrarea: `deschide()` (chemata DOAR de nvr-web, la pornire) importa events.jsonl daca baza
e goala si il redenumeste events.jsonl.migrat (ramane ca rezerva, nu se mai citeste).
Deschiderea lenesa din `_connect()` nu migreaza niciodata — nvr-ai nu e proprietar.
`t` ramane ora LOCALA ca text (afisarea din UI), `ts` e epoch-ul (unic si in noaptea
schimbarii orei); filtrul pe zi se face pe intervalul [00:00, 24:00) local tradus in epoch.
"""
import calendar
import json
import os
import sqlite3
import threading
import time
import uuid
import logare
import schema

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_FILE = os.path.join(ROOT, "data", "events.db")
JSONL_FILE = os.path.join(ROOT, "data", "events.jsonl")
KEEP_DAYS = 365          # implicit; `events_keep_days` din Setari are prioritate
TRIM_EVERY = 3600        # curatarea dupa varsta ruleaza cel mult o data pe ora
SEV_RANK = {"info": 0, "atentie": 1, "critic": 2}
FIXE = ("id", "t", "tip", "sev", "cam")     # coloane proprii; restul merge in JSON

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    rowid INTEGER PRIMARY KEY,
    id    TEXT    NOT NULL UNIQUE,
    ts    INTEGER NOT NULL,
    t     TEXT    NOT NULL,
    tip   TEXT    NOT NULL,
    sev   TEXT    NOT NULL DEFAULT 'info',
    cam   INTEGER,
    date  TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ev_ts     ON events(ts);
CREATE INDEX IF NOT EXISTS ev_cam_ts ON events(cam, ts);
CREATE INDEX IF NOT EXISTS ev_tip_ts ON events(tip, ts);
CREATE INDEX IF NOT EXISTS ev_sev_ts ON events(sev, ts);
"""
# migrari viitoare ale tabelelor de aici: {2: "ALTER TABLE events ADD COLUMN ...", 3: functie(conn)}
MIGRARI = {}


_lock = threading.RLock()
lock = _lock             # si pentru modulele care tin tabele in aceeasi baza (marcaje)
_conn = None
_last_trim = 0.0


def _epoch(t):
    """'YYYY-MM-DD HH:MM:SS' local -> epoch. Ambiguu doar in ora repetata la trecerea
    la ora de iarna — pentru randurile vechi (migrate) nu avem altceva."""
    return int(time.mktime(time.strptime(t, "%Y-%m-%d %H:%M:%S")))


def _connect():
    """Conexiunea unica, deschisa lenes (importul modulului nu atinge discul — nvr-ai
    importa `rules` fara sa aiba treaba cu jurnalul)."""
    global _conn
    if _conn is not None:
        return _conn
    with _lock:
        if _conn is not None:
            return _conn
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        c = sqlite3.connect(DB_FILE, check_same_thread=False, isolation_level=None,
                            timeout=5)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        schema.aplica(c, "jurnal", SCHEMA, MIGRARI)
        for f in (DB_FILE, DB_FILE + "-wal", DB_FILE + "-shm"):
            try:
                os.chmod(f, 0o600)         # nume de persoane, numere de masini
            except OSError:
                pass
        _conn = c
        return c


def conexiune():
    """Conexiunea partajata — pentru tabelele altor module din aceeasi baza (marcaje.py).
    Orice acces trece prin `jurnal.lock`."""
    return _connect()


def deschide():
    """Deschide si migreaza (daca e cazul) baza ACUM — chemata la pornirea nvr-web (singurul
    proprietar), ca importul celor ~225k de randuri vechi sa nu cada intr-un fir de cerere."""
    _migrate(_connect())


def reset():
    """Inchide conexiunea (teste: dupa ce DB_FILE a fost redirectat)."""
    global _conn, _last_trim
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None
        _last_trim = 0.0


def _row(entry, ts=None):
    """Dictionarul plat al unui eveniment -> tuplul de inserat. Tolerant: fara id ->
    generam; fara sev -> info; fara t -> acum. `cam` lipsa/None ramane NULL."""
    e = dict(entry)
    t = e.get("t") or time.strftime("%Y-%m-%d %H:%M:%S")
    if ts is None:
        ts = _epoch(t)
    cam = e.get("cam")
    rest = {k: v for k, v in e.items() if k not in FIXE}
    return (e.get("id") or uuid.uuid4().hex[:12], int(ts), t, str(e.get("tip") or "?"),
            e.get("sev") or "info", int(cam) if cam is not None else None,
            json.dumps(rest, ensure_ascii=False))


INSERT = ("INSERT OR IGNORE INTO events (id, ts, t, tip, sev, cam, date) "
          "VALUES (?, ?, ?, ?, ?, ?, ?)")


def insert(entry, ts=None):
    """Scrie un eveniment (dictionar plat, ca cel intors de query). Apoi, cel mult o
    data pe ora, sterge ce a trecut de retentie."""
    global _last_trim
    c = _connect()
    with _lock:
        c.execute(INSERT, _row(entry, ts))
    now = time.time()
    if now - _last_trim >= TRIM_EVERY:
        _last_trim = now
        trim()


def _keep_days():
    try:
        import settings
        return max(7, int(settings.get("events_keep_days") or KEEP_DAYS))
    except Exception:
        return KEEP_DAYS


def trim(keep_days=None):
    """Sterge evenimentele mai vechi de `keep_days` zile. Intoarce cate a sters."""
    days = keep_days or _keep_days()
    c = _connect()
    with _lock:
        cur = c.execute("DELETE FROM events WHERE ts < ?", (int(time.time()) - days * 86400,))
        return cur.rowcount


def _day_range(day):
    """[inceput, sfarsit) in epoch pentru ziua LOCALA 'YYYY-MM-DD' (23/25h la DST).
    O zi invalida intoarce None -> query nu potriveste nimic (ca inainte)."""
    try:
        y, m, d = (int(x) for x in day.split("-"))
        start = time.mktime((y, m, d, 0, 0, 0, 0, 0, -1))
        end = time.mktime((y, m, d + 1, 0, 0, 0, 0, 0, -1))   # mktime normalizeaza 32 ian
        return int(start), int(end)
    except (ValueError, OverflowError, AttributeError):
        return None


def query(limit=200, tip=None, cam=None, day=None, min_sev=None, since=None, until=None):
    """Cele mai noi evenimente, cele mai noi primele, cu filtre optionale:
    tip, cam (id), day ('YYYY-MM-DD' local), min_sev ('atentie' = atentie+critic),
    since/until (epoch, [since, until)). Intoarce dictionare plate ca in JSONL-ul vechi,
    plus `ts` (epoch)."""
    cond, args = [], []
    if tip:
        cond.append("tip = ?")
        args.append(tip)
    if cam is not None:
        cond.append("cam = ?")
        args.append(int(cam))
    if day:
        rng = _day_range(day)
        if rng is None:
            return []
        cond.append("ts >= ? AND ts < ?")
        args.extend(rng)
    if since is not None:
        cond.append("ts >= ?")
        args.append(int(since))
    if until is not None:
        cond.append("ts < ?")
        args.append(int(until))
    prag = SEV_RANK.get(min_sev, -1)
    if prag > 0:
        sevs = [s for s, r in SEV_RANK.items() if r >= prag]
        cond.append("sev IN (%s)" % ",".join("?" * len(sevs)))
        args.extend(sevs)
    sql = "SELECT id, t, tip, sev, cam, date, ts FROM events"
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY ts DESC, rowid DESC LIMIT ?"
    args.append(max(1, int(limit)))
    c = _connect()
    with _lock:
        rows = c.execute(sql, args).fetchall()
    out = []
    for id_, t, tip_, sev, cam_, date, ts in rows:
        # `ts` (epoch) vine gratis din baza si scuteste consumatorii de strptime pe
        # mii de randuri (episoadele: 5000 x strptime = ~120 ms curat, secunde sub load)
        e = {"id": id_, "t": t, "tip": tip_, "sev": sev, "ts": ts}
        if cam_ is not None:
            e["cam"] = cam_
        try:
            e.update(json.loads(date))
        except ValueError:
            pass
        out.append(e)
    return out


def stats():
    """{evenimente, cel_mai_vechi, cel_mai_nou, mb} — pentru pagina Stare."""
    c = _connect()
    with _lock:
        n, lo, hi = c.execute("SELECT COUNT(*), MIN(t), MAX(t) FROM events").fetchone()
    try:
        octeti = os.path.getsize(DB_FILE) + os.path.getsize(DB_FILE + "-wal")
    except OSError:
        octeti = 0
    return {"evenimente": n, "cel_mai_vechi": lo, "cel_mai_nou": hi,
            "mb": round(octeti / 2**20, 1)}


def _migrate(c):
    """Importa events.jsonl (formatul vechi) daca baza e goala, apoi il redenumeste.
    Liniile stricate se sar; fara `t` nu avem ce ordona, deci si ele se sar."""
    if not os.path.exists(JSONL_FILE):
        return
    if c.execute("SELECT 1 FROM events LIMIT 1").fetchone():
        return
    t0 = time.time()
    n = sarite = 0
    lot = []
    with _lock, open(JSONL_FILE, "rb") as f:
        c.execute("BEGIN")
        for ln in f:
            try:
                e = json.loads(ln)
                if not isinstance(e, dict) or not e.get("t"):
                    raise ValueError
                lot.append(_row(e))
            except (ValueError, TypeError, OverflowError):
                sarite += 1
                continue
            if len(lot) >= 5000:
                c.executemany(INSERT, lot)
                n += len(lot)
                lot = []
        if lot:
            c.executemany(INSERT, lot)
            n += len(lot)
        c.execute("COMMIT")
    try:
        os.replace(JSONL_FILE, JSONL_FILE + ".migrat")
    except OSError:
        pass
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # altfel WAL-ul ramane ~100MB
    logare.info(f"[jurnal] migrat events.jsonl -> events.db: {n} evenimente"
          f"{f', {sarite} linii sarite' if sarite else ''} in {time.time() - t0:.1f}s")
