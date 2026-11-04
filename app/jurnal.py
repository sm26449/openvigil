#!/usr/bin/env python3
"""Jurnalul permanent de evenimente — PostgreSQL (tabelul `events`, vezi app/db.py).

Pana pe 2026-10-23 jurnalul era un JSONL; apoi SQLite (WAL); din 2.0.0 e in PostgreSQL.
Fiecare eveniment e un rand cu timp UTC (epoch `ts`), ora locala ca text (`t`), camera,
tip, severitate si restul campurilor ca JSON (`date`); indecsii pe (ts), (cam, ts),
(tip, ts), (sev, ts) fac orice filtru al lui `query()` o citire de cateva ms.

Contractul cu restul sistemului NU se schimba: `rules._journal()` scrie, `rules.journal()`
citeste, si ambele intorc/primesc aceleasi dictionare plate ca inainte (id, t, tip, sev,
cam, + campurile evenimentului). `t` ramane ora LOCALA ca text (afisarea din UI), `ts` e
epoch-ul (unic si in noaptea schimbarii orei); filtrul pe zi se face pe intervalul
[00:00, 24:00) local tradus in epoch.

Ordinea in aceeasi secunda vine din coloana `seq` (IDENTITY), care inlocuieste `rowid`-ul
SQLite. Cautarea de text foloseste `ILIKE` (in PostgreSQL `LIKE` e sensibil la litere mari),
cu potrivirea reala, fara diacritice, facuta apoi in Python.
"""
import json
import os
import threading
import time
import uuid

import db
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEEP_DAYS = 365          # implicit; `events_keep_days` din Setari are prioritate
TRIM_EVERY = 3600        # curatarea dupa varsta ruleaza cel mult o data pe ora
SEV_RANK = {"info": 0, "atentie": 1, "critic": 2}
FIXE = ("id", "t", "tip", "sev", "cam")     # coloane proprii; restul merge in JSON

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq   BIGINT  GENERATED ALWAYS AS IDENTITY,
    id    TEXT    PRIMARY KEY,
    ts    BIGINT  NOT NULL,
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
# migrari viitoare ale tabelului: {2: "ALTER TABLE events ADD COLUMN ...", 3: functie(conn)}
MIGRARI = {}

_lock = threading.RLock()
lock = _lock             # pastrat pentru compatibilitate; PostgreSQL nu mai cere serializare in proces
_last_trim = 0.0
_gata = False


def _epoch(t):
    """'YYYY-MM-DD HH:MM:SS' local -> epoch. Ambiguu doar in ora repetata la trecerea
    la ora de iarna — pentru randurile vechi (migrate) nu avem altceva."""
    return int(time.mktime(time.strptime(t, "%Y-%m-%d %H:%M:%S")))


def _init():
    global _gata
    if not _gata:
        db.aplica("jurnal", SCHEMA, MIGRARI)
        _gata = True


def deschide():
    """Asigura schema ACUM — chemata la pornirea nvr-web. (Inainte importa events.jsonl;
    din 2.0.0 migrarea datelor vechi se face o singura data cu scripts/migrare_pg.py.)"""
    _init()


def reset():
    """Pentru teste: uita ca schema a fost verificata (si contorul de curatare)."""
    global _gata, _last_trim
    _gata = False
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


INSERT = ("INSERT INTO events (id, ts, t, tip, sev, cam, date) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING")


def insert(entry, ts=None):
    """Scrie un eveniment (dictionar plat, ca cel intors de query). Apoi, cel mult o
    data pe ora, sterge ce a trecut de retentie."""
    global _last_trim
    _init()
    with db.conn() as c:
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
    _init()
    with db.conn() as c:
        return c.execute("DELETE FROM events WHERE ts < %s",
                         (int(time.time()) - days * 86400,)).rowcount


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


_FOLD = str.maketrans("ăâîșțĂÂÎȘȚşţŞŢ", "aaistAAISTstST")


def fara_diacritice(s):
    return str(s or "").translate(_FOLD).lower()


def _tipar_text(text):
    """Tiparul LIKE pentru un fragment, insensibil la diacritice: literele care pot purta
    diacritice (a, i, s, t) devin `_`, restul se scapa. Potrivirea exacta se face apoi in Python."""
    out = []
    for ch in fara_diacritice(text)[:80]:
        if ch in "aist":
            out.append("_")
        elif ch in "\\%_":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "%" + "".join(out) + "%"


def query(limit=200, tip=None, cam=None, day=None, min_sev=None, since=None, until=None, text=None):
    """Cele mai noi evenimente, cele mai noi primele, cu filtre optionale:
    tip, cam (id), day ('YYYY-MM-DD' local), min_sev ('atentie' = atentie+critic),
    since/until (epoch, [since, until)), text (fragment cautat in campurile evenimentului).
    Intoarce dictionare plate ca in JSONL-ul vechi, plus `ts` (epoch)."""
    _init()
    cond, args = [], []
    if tip:
        cond.append("tip = %s")
        args.append(tip)
    if text:
        cond.append("date ILIKE %s ESCAPE '\\'")   # PostgreSQL: LIKE e sensibil la litere mari
        args.append(_tipar_text(text))
    if cam is not None:
        cond.append("cam = %s")
        args.append(int(cam))
    if day:
        rng = _day_range(day)
        if rng is None:
            return []
        cond.append("ts >= %s AND ts < %s")
        args.extend(rng)
    if since is not None:
        cond.append("ts >= %s")
        args.append(int(since))
    if until is not None:
        cond.append("ts < %s")
        args.append(int(until))
    prag = SEV_RANK.get(min_sev, -1)
    if prag > 0:
        sevs = [s for s, r in SEV_RANK.items() if r >= prag]
        ph = ",".join(["%s"] * len(sevs))
        cond.append(f"sev IN ({ph})")
        args.extend(sevs)
    sql = "SELECT id, t, tip, sev, cam, date, ts FROM events"
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY ts DESC, seq DESC LIMIT %s"
    args.append(max(1, int(limit)))
    with db.conn() as c:
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
        if text and fara_diacritice(text)[:80] not in fara_diacritice(date):
            continue                    # tiparul cu `_` e larg; aici potrivirea reala, fara diacritice
        out.append(e)
    return out


def stats():
    """{evenimente, cel_mai_vechi, cel_mai_nou, mb} — pentru pagina Stare."""
    _init()
    with db.conn() as c:
        n, lo, hi = c.execute("SELECT COUNT(*), MIN(t), MAX(t) FROM events").fetchone()
        octeti = c.execute("SELECT pg_total_relation_size('events')").fetchone()[0] or 0
    return {"evenimente": n, "cel_mai_vechi": lo, "cel_mai_nou": hi,
            "mb": round(octeti / 2**20, 1)}
