#!/usr/bin/env python3
"""Marcaje (bookmarks) pe inregistrari: un moment sau un interval pe o camera, cu eticheta.

Echivalentul "labels and bookmarks" din Synology: ai gasit ceva in redare, apesi Marcaj,
scrii doua cuvinte, si momentul ramane pe timeline (Inregistrari + HD) ca sa-l regasesti
tu sau altcineva maine. Un marcaj se poate pune direct la dosar (proba cu intervalul lui).

Stau in aceeasi baza SQLite ca jurnalul (data/events.db, vezi jurnal.py) — tabelul e al
acestui modul, conexiunea si lock-ul sunt ale lui jurnal. Timpii: `t`/`t_end` ora locala
ca text (afisare, acelasi format ca jurnalul), `ts`/`ts_end` epoch (filtrare pe zi).
"""
import time
import uuid

import jurnal
from i18n import t

SCHEMA = """
CREATE TABLE IF NOT EXISTS marcaje (
    id       TEXT    PRIMARY KEY,
    cam      INTEGER NOT NULL,
    ts       INTEGER NOT NULL,
    ts_end   INTEGER NOT NULL,
    t        TEXT    NOT NULL,
    t_end    TEXT    NOT NULL,
    eticheta TEXT    NOT NULL DEFAULT '',
    nota     TEXT    NOT NULL DEFAULT '',
    creat_de TEXT,
    creat    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS marcaje_cam_ts ON marcaje(cam, ts);
CREATE INDEX IF NOT EXISTS marcaje_ts ON marcaje(ts);
"""
COLS = ("id", "cam", "t", "t_end", "eticheta", "nota", "creat_de", "creat")
MAX_DURATA = 24 * 3600
_gata = None      # conexiunea pe care am verificat schema (jurnal.reset() da una noua)


def _db():
    global _gata
    c = jurnal.conexiune()
    if _gata is not c:
        with jurnal.lock:
            c.executescript(SCHEMA)
        _gata = c
    return c


def _norm_t(t):
    """Accepta 'YYYY-MM-DD HH:MM:SS' sau cu 'T'; intoarce (text local, epoch)."""
    t = str(t or "").strip().replace("T", " ")[:19]
    return t, jurnal._epoch(t)        # ValueError daca nu e data


def _dict(row):
    d = dict(zip(COLS, row))
    d["durata"] = int(jurnal._epoch(d["t_end"]) - jurnal._epoch(d["t"]))
    return d


def create(cam, start, end=None, eticheta="", nota="", who=None):
    """Un marcaj nou. `end` lipsa = moment punctual. Intoarce marcajul creat."""
    cam = int(cam)
    tx, ts = _norm_t(start)
    t_end, ts_end = _norm_t(end) if end else (tx, ts)
    if ts_end < ts:
        raise ValueError(t("sfarsitul e inaintea inceputului"))
    if ts_end - ts > MAX_DURATA:
        raise ValueError(t("un marcaj ține cel mult 24 de ore"))
    eticheta = (eticheta or "").strip()[:60]
    nota = (nota or "").strip()[:500]
    mid = uuid.uuid4().hex[:12]
    creat = time.strftime("%Y-%m-%d %H:%M:%S")
    c = _db()
    with jurnal.lock:
        c.execute("INSERT INTO marcaje (id, cam, ts, ts_end, t, t_end, eticheta, nota, "
                  "creat_de, creat) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (mid, cam, ts, ts_end, tx, t_end, eticheta, nota, who, creat))
    return get(mid)


def get(mid):
    c = _db()
    with jurnal.lock:
        row = c.execute(f"SELECT {','.join(COLS)} FROM marcaje WHERE id = ?", (mid,)).fetchone()
    return _dict(row) if row else None


def lista(cam=None, day=None, limit=500, visible=None):
    """Marcajele, cele mai noi primele. cam/day (zi locala) filtreaza; visible = multime de
    camere permise (None = toate). Un marcaj-interval apare in ziua in care ATINGE ziua."""
    cond, args = [], []
    if cam is not None:
        cond.append("cam = ?")
        args.append(int(cam))
    if day:
        rng = jurnal._day_range(day)
        if rng is None:
            return []
        cond.append("ts < ? AND ts_end >= ?")
        args.extend((rng[1], rng[0]))
    if visible is not None:
        vis = sorted(int(v) for v in visible)
        if not vis:
            return []
        cond.append("cam IN (%s)" % ",".join("?" * len(vis)))
        args.extend(vis)
    sql = f"SELECT {','.join(COLS)} FROM marcaje"
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY ts DESC LIMIT ?"
    args.append(max(1, int(limit)))
    c = _db()
    with jurnal.lock:
        rows = c.execute(sql, args).fetchall()
    return [_dict(r) for r in rows]


def update(mid, eticheta=None, nota=None, end=None):
    """Schimba eticheta / nota / sfarsitul. Intoarce marcajul sau None daca nu exista."""
    m = get(mid)
    if not m:
        return None
    sets, args = [], []
    if eticheta is not None:
        sets.append("eticheta = ?")
        args.append(str(eticheta).strip()[:60])
    if nota is not None:
        sets.append("nota = ?")
        args.append(str(nota).strip()[:500])
    if end is not None:
        t_end, ts_end = _norm_t(end)
        if ts_end < jurnal._epoch(m["t"]) or ts_end - jurnal._epoch(m["t"]) > MAX_DURATA:
            raise ValueError(t("sfârșit invalid"))
        sets.append("t_end = ?")
        args.append(t_end)
        sets.append("ts_end = ?")
        args.append(ts_end)
    if sets:
        c = _db()
        with jurnal.lock:
            c.execute(f"UPDATE marcaje SET {', '.join(sets)} WHERE id = ?", (*args, mid))
    return get(mid)


def delete(mid):
    c = _db()
    with jurnal.lock:
        return c.execute("DELETE FROM marcaje WHERE id = ?", (mid,)).rowcount > 0
