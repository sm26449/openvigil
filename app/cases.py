#!/usr/bin/env python3
"""Pachetul de caz — un dosar de probe pentru un incident, gata de predat.

Cand un operator vede ceva grav, aduna mai multe momente (interval pe o camera)
intr-un singur "caz". La final exporta un ZIP: fiecare clip cu watermark (camera +
ora gravate in imagine), plus manifest.json cu descrierea si suma SHA-256 a fiecarui
fisier — asa dosarul e verificabil si nu poate fi umblat pe ascuns.

Aici e doar metadata: CRUD-ul cazurilor (`dosare`) si al probelor (`dosar_probe`).
Stau in PostgreSQL (vezi app/db.py) din 2.0.x — inainte stateau in data/cases.json,
importat o data la pornire (vezi `_migra_json`). Randarea clipurilor si impachetarea
ZIP le face routes_incidents.py (are acces la ffmpeg si la NVR).

Probele pastreaza timpii exact cum vin din UI (timestamp Hik compact „…Z", dati asa
mai departe la ffmpeg). Footage-ul pus intr-un dosar se auto-protejeaza de retentie
printr-o intrare in tabelul `probe` (vezi _protejeaza_footage / app/probe.py).
"""
import json
import os
import time
import uuid

import db
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES_FILE = os.path.join(ROOT, "data", "cases.json")   # vechea stocare JSON (importata o data)

SCHEMA = """
CREATE TABLE IF NOT EXISTS dosare (
    seq       BIGINT GENERATED ALWAYS AS IDENTITY,
    id        TEXT    PRIMARY KEY,
    titlu     TEXT    NOT NULL,
    descriere TEXT    NOT NULL DEFAULT '',
    creat_de  TEXT,
    creat     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS dosar_probe (
    seq        BIGINT  GENERATED ALWAYS AS IDENTITY,
    id         TEXT    PRIMARY KEY,
    dosar      TEXT    NOT NULL REFERENCES dosare(id) ON DELETE CASCADE,
    cam        INTEGER NOT NULL,
    inceput    TEXT    NOT NULL,
    sfarsit    TEXT    NOT NULL,
    nota       TEXT    NOT NULL DEFAULT '',
    event_id   TEXT,
    adaugat_de TEXT,
    adaugat    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS dosar_probe_dosar ON dosar_probe(dosar, seq);
"""
MIGRARI = {}      # migrari viitoare: {2: "ALTER TABLE ..."}

_CAZ_COLS = ("id", "titlu", "descriere", "creat_de", "creat")
_PROBA_COLS = ("id", "cam", "inceput", "sfarsit", "nota", "event_id", "adaugat_de", "adaugat")
_gata = False


def _init():
    global _gata
    if not _gata:
        db.aplica("dosare", SCHEMA, MIGRARI)
        _migra_json()
        _gata = True


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _proba_dict(row):
    """Rand din dosar_probe -> forma de UI (inceput/sfarsit se arata ca start/end)."""
    d = dict(zip(_PROBA_COLS, row))
    return {"id": d["id"], "cam": int(d["cam"]), "start": d["inceput"], "end": d["sfarsit"],
            "nota": d["nota"], "event_id": d["event_id"],
            "adaugat_de": d["adaugat_de"], "adaugat": d["adaugat"]}


def create(titlu, who=None, descriere=""):
    """Deschide un caz nou. Returneaza cazul creat (cu lista de probe goala)."""
    _init()
    titlu = (titlu or "").strip()
    if not titlu:
        raise ValueError(t("titlul cazului e obligatoriu"))
    cid = uuid.uuid4().hex[:12]
    with db.conn() as c:
        c.execute("INSERT INTO dosare (id, titlu, descriere, creat_de, creat) "
                  "VALUES (%s,%s,%s,%s,%s)",
                  (cid, titlu, (descriere or "").strip(), who, _now()))
    return get(cid)


def lista():
    """Toate cazurile, cel mai nou primul, fiecare cu probele lui si `nr_probe`.
    Probele sunt incluse pentru ca scoping-ul (vizibilitate per camera) le citeste."""
    _init()
    with db.conn() as c:
        cazuri = c.execute(
            f"SELECT {','.join(_CAZ_COLS)} FROM dosare ORDER BY seq DESC").fetchall()
        probe = c.execute(
            f"SELECT dosar, {','.join(_PROBA_COLS)} FROM dosar_probe ORDER BY seq").fetchall()
    pe_dosar = {}
    for r in probe:
        pe_dosar.setdefault(r[0], []).append(_proba_dict(r[1:]))
    out = []
    for row in cazuri:
        c0 = dict(zip(_CAZ_COLS, row))
        c0["probe"] = pe_dosar.get(c0["id"], [])
        c0["nr_probe"] = len(c0["probe"])
        out.append(c0)
    return out


def get(cid):
    """Cazul cu toate probele (ordinea adaugarii), sau None daca nu exista."""
    _init()
    with db.conn() as c:
        row = c.execute(
            f"SELECT {','.join(_CAZ_COLS)} FROM dosare WHERE id = %s", (cid,)).fetchone()
        if not row:
            return None
        probe = c.execute(
            f"SELECT {','.join(_PROBA_COLS)} FROM dosar_probe WHERE dosar = %s ORDER BY seq",
            (cid,)).fetchall()
    c0 = dict(zip(_CAZ_COLS, row))
    c0["probe"] = [_proba_dict(r) for r in probe]
    return c0


def delete(cid):
    """Sterge cazul; probele dispar cu el (ON DELETE CASCADE)."""
    _init()
    with db.conn() as c:
        return c.execute("DELETE FROM dosare WHERE id = %s", (cid,)).rowcount > 0


def _compact_local(compact):
    """'20260803T102030Z' -> 'YYYY-MM-DD HH:MM:SS' (ora locala, ca in restul interfetei).
    None daca formatul nu se potriveste."""
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.strptime(str(compact), "%Y%m%dT%H%M%SZ"))
    except (ValueError, TypeError):
        return None


def _protejeaza_footage(cid, cam, start, end, who):
    """Footage-ul pus intr-un dosar se auto-protejeaza de retentie (sursa='dosar:<cid>').
    Best-effort: o problema la baza nu trebuie sa impiedice construirea dosarului."""
    a, b = _compact_local(start), _compact_local(end)
    if not a or not b:
        return
    try:
        import probe
        probe.create(cam, a, b, motiv=t("probă în dosar"), sursa=f"dosar:{cid}", who=who)
    except Exception:
        pass


def add_proba(cid, cam, start, end, nota="", event_id=None, who=None):
    """Adauga o proba (interval pe o camera) la caz. start/end = timestamp Hik (…Z).
    Footage-ul se auto-protejeaza de retentie (vezi _protejeaza_footage). Intoarce cazul
    actualizat; ridica KeyError daca dosarul nu exista."""
    _init()
    with db.conn() as c:
        if not c.execute("SELECT 1 FROM dosare WHERE id = %s", (cid,)).fetchone():
            raise KeyError("caz inexistent")
        pid = uuid.uuid4().hex[:8]
        c.execute("INSERT INTO dosar_probe (id, dosar, cam, inceput, sfarsit, nota, event_id, "
                  "adaugat_de, adaugat) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                  (pid, cid, int(cam), start, end, (nota or "").strip(), event_id, who, _now()))
    _protejeaza_footage(cid, cam, start, end, who)
    return get(cid)


def del_proba(cid, pid):
    """Scoate o proba dintr-un caz. Ridica KeyError daca dosarul nu exista; intoarce
    True daca proba a fost gasita si stearsa."""
    _init()
    with db.conn() as c:
        if not c.execute("SELECT 1 FROM dosare WHERE id = %s", (cid,)).fetchone():
            raise KeyError("caz inexistent")
        return c.execute("DELETE FROM dosar_probe WHERE dosar = %s AND id = %s",
                         (cid, pid)).rowcount > 0


def _insert_caz_json(c, cid, caz):
    """Un caz din vechiul JSON -> randuri in dosare/dosar_probe (ON CONFLICT DO NOTHING)."""
    cid = str(caz.get("id") or cid)
    c.execute("INSERT INTO dosare (id, titlu, descriere, creat_de, creat) "
              "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING",
              (cid, (caz.get("titlu") or "").strip() or cid, (caz.get("descriere") or "").strip(),
               caz.get("creat_de"), caz.get("creat") or _now()))
    for p in caz.get("probe", []):
        try:
            cam = int(p.get("cam", 0))
        except (ValueError, TypeError):
            cam = 0
        c.execute("INSERT INTO dosar_probe (id, dosar, cam, inceput, sfarsit, nota, event_id, "
                  "adaugat_de, adaugat) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                  "ON CONFLICT (id) DO NOTHING",
                  (str(p.get("id") or uuid.uuid4().hex[:8]), cid, cam,
                   str(p.get("start") or ""), str(p.get("end") or ""),
                   (p.get("nota") or "").strip(), p.get("event_id"),
                   p.get("adaugat_de"), p.get("adaugat") or _now()))


def _migra_json():
    """Import unic al vechiului data/cases.json (dinainte de PostgreSQL). Ruleaza o data:
    daca tabelul e gol si fisierul exista, muta cazurile in baza, apoi redenumeste fisierul
    .migrat ca sa nu se reia. Best-effort, sub advisory lock (web poate avea mai multi workeri).
    """
    if not os.path.exists(CASES_FILE):
        return
    try:
        with open(CASES_FILE) as f:
            data = json.load(f)
    except (ValueError, OSError):
        return
    if not isinstance(data, dict):
        return
    muta = False
    try:
        with db.conn() as c:
            key = db._lock_key("dosare_migrare_json")
            c.execute("SELECT pg_advisory_lock(%s)", (key,))
            try:
                gol = c.execute("SELECT 1 FROM dosare LIMIT 1").fetchone() is None
                if gol and data:
                    with c.transaction():
                        for cid, caz in data.items():
                            if isinstance(caz, dict):
                                _insert_caz_json(c, cid, caz)
                muta = True      # fie am importat, fie baza avea deja dosare — fisierul e vechi
            finally:
                c.execute("SELECT pg_advisory_unlock(%s)", (key,))
    except Exception:
        return
    if muta:
        try:
            os.replace(CASES_FILE, CASES_FILE + ".migrat")
        except OSError:
            pass
