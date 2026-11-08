#!/usr/bin/env python3
"""Probe: intervale de inregistrare marcate „pastreaza ca proba", scutite de retentie.

Retentia (app/record.py) sterge segmentele dupa zile, iar garda de spatiu taie si din cele
neexpirate cand discul se umple — inclusiv footage cu evenimente. O proba spune „asta NU se
sterge niciodata automat": un interval pe o camera pe care curatarea il sare, iar la disc
plin se alarmeaza tare in loc sa-l taie.

Spre deosebire de marcaje (bookmarks, bornele de pe timeline), o proba e un concept de sine
statator, legat de lantul de custodie: are motiv, poate avea termen (`pana_la`, altfel pe
termen nedefinit) si o sursa (manual, sau dosarul care a cerut-o). Footage-ul pus intr-un
dosar de caz se auto-protejeaza prin aici (vezi cases.add_proba).

Stau in PostgreSQL (vezi app/db.py), tabelul `probe`. Timpii: `t`/`t_end` ora locala ca text
(afisare, acelasi format ca jurnalul si marcajele), `ts`/`ts_end` epoch (filtrare + retentie).
Helperele de timp (`_epoch`, `_day_range`) sunt in jurnal.py — pure, fara baza.
"""
import time
import uuid

import jurnal
import db
from i18n import t

SCHEMA = """
CREATE TABLE IF NOT EXISTS probe (
    id       TEXT    PRIMARY KEY,
    cam      INTEGER NOT NULL,
    ts       BIGINT  NOT NULL,
    ts_end   BIGINT  NOT NULL,
    t        TEXT    NOT NULL,
    t_end    TEXT    NOT NULL,
    motiv    TEXT    NOT NULL DEFAULT '',
    nota     TEXT    NOT NULL DEFAULT '',
    pana_la  BIGINT,
    sursa    TEXT    NOT NULL DEFAULT 'manual',
    creat_de TEXT,
    creat    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS probe_cam_ts ON probe(cam, ts);
CREATE INDEX IF NOT EXISTS probe_pana_la ON probe(pana_la);
"""
COLS = ("id", "cam", "t", "t_end", "motiv", "nota", "pana_la", "sursa", "creat_de", "creat")
MAX_DURATA = 7 * 24 * 3600      # o proba acopera cel mult 7 zile (o protectie accidentala a arhivei intregi)

MIGRARI = {}      # migrari viitoare: {2: "ALTER TABLE ..."}
_gata = False


def _init():
    global _gata
    if not _gata:
        db.aplica("probe", SCHEMA, MIGRARI)
        _gata = True


def _norm_t(x):
    """Accepta 'YYYY-MM-DD HH:MM:SS' sau cu 'T'; intoarce (text local, epoch)."""
    x = str(x or "").strip().replace("T", " ")[:19]
    return x, jurnal._epoch(x)        # ValueError daca nu e data


def _dict(row):
    d = dict(zip(COLS, row))
    d["durata"] = int(jurnal._epoch(d["t_end"]) - jurnal._epoch(d["t"]))
    d["expira"] = bool(d["pana_la"])
    return d


def create(cam, start, end, motiv="", nota="", pana_la=None, sursa="manual", who=None):
    """O proba noua (interval pe o camera). `pana_la` = epoch dupa care protectia expira
    (None = pe termen nedefinit). Intoarce proba creata."""
    _init()
    cam = int(cam)
    tx, ts = _norm_t(start)
    t_end, ts_end = _norm_t(end)
    if ts_end < ts:
        raise ValueError(t("sfarsitul e inaintea inceputului"))
    if ts_end - ts > MAX_DURATA:
        raise ValueError(t("o probă acoperă cel mult 7 zile"))
    motiv = (motiv or "").strip()[:120]
    nota = (nota or "").strip()[:500]
    pl = int(pana_la) if pana_la else None
    pid = uuid.uuid4().hex[:12]
    creat = time.strftime("%Y-%m-%d %H:%M:%S")
    with db.conn() as c:
        c.execute("INSERT INTO probe (id, cam, ts, ts_end, t, t_end, motiv, nota, pana_la, "
                  "sursa, creat_de, creat) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (pid, cam, ts, ts_end, tx, t_end, motiv, nota, pl, sursa, who, creat))
    return get(pid)


def get(pid):
    _init()
    with db.conn() as c:
        row = c.execute(f"SELECT {','.join(COLS)} FROM probe WHERE id = %s", (pid,)).fetchone()
    return _dict(row) if row else None


def lista(cam=None, day=None, limit=500, visible=None):
    """Probele, cele mai noi primele. cam/day (zi locala) filtreaza; visible = multime de
    camere permise (None = toate). O proba-interval apare in ziua in care ATINGE ziua."""
    _init()
    cond, args = [], []
    if cam is not None:
        cond.append("cam = %s")
        args.append(int(cam))
    if day:
        rng = jurnal._day_range(day)
        if rng is None:
            return []
        cond.append("ts < %s AND ts_end >= %s")
        args.extend((rng[1], rng[0]))
    if visible is not None:
        vis = sorted(int(v) for v in visible)
        if not vis:
            return []
        cond.append("cam IN (%s)" % ",".join(["%s"] * len(vis)))
        args.extend(vis)
    sql = f"SELECT {','.join(COLS)} FROM probe"
    if cond:
        sql += " WHERE " + " AND ".join(cond)
    sql += " ORDER BY ts DESC LIMIT %s"
    args.append(max(1, int(limit)))
    with db.conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_dict(r) for r in rows]


def update(pid, motiv=None, nota=None, pana_la="keep"):
    """Schimba motivul / nota / termenul. `pana_la`: 'keep' = neschimbat, None = pe termen
    nedefinit, un epoch = pana atunci. Intoarce proba sau None daca nu exista."""
    p = get(pid)
    if not p:
        return None
    sets, args = [], []
    if motiv is not None:
        sets.append("motiv = %s")
        args.append(str(motiv).strip()[:120])
    if nota is not None:
        sets.append("nota = %s")
        args.append(str(nota).strip()[:500])
    if pana_la != "keep":
        sets.append("pana_la = %s")
        args.append(int(pana_la) if pana_la else None)
    if sets:
        with db.conn() as c:
            c.execute(f"UPDATE probe SET {', '.join(sets)} WHERE id = %s", (*args, pid))
    return get(pid)


def delete(pid):
    _init()
    with db.conn() as c:
        return c.execute("DELETE FROM probe WHERE id = %s", (pid,)).rowcount > 0


def active_ranges(now=None):
    """{cam: [(ts, ts_end), ...]} — doar protectiile inca in vigoare (fara termen, sau cu
    termen in viitor). Pentru retentie: un singur apel, intervale sortate pe camera. Daca
    baza nu e disponibila, ridica — apelantul (record.prune) prinde si NU sterge nimic
    in tura aia (fail-safe: mai bine pastram mai mult decat sa taiem o proba din greseala)."""
    _init()
    now = int(now or time.time())
    with db.conn() as c:
        rows = c.execute("SELECT cam, ts, ts_end FROM probe WHERE pana_la IS NULL OR "
                         "pana_la > %s ORDER BY cam, ts", (now,)).fetchall()
    out = {}
    for cam, ts, ts_end in rows:
        out.setdefault(int(cam), []).append((int(ts), int(ts_end)))
    return out


def acopera(ranges, a, b):
    """True daca intervalul [a, b] atinge vreun interval protejat din `ranges`
    (lista de (ts, ts_end) a unei camere). Overlap simplu; listele sunt scurte."""
    if not ranges:
        return False
    for s, e in ranges:
        if s <= b and a <= e:
            return True
    return False
