#!/usr/bin/env python3
"""Scorerul de falsuri (fals-pozitive) — memoria spatiala + semantica a verdictelor.

Problema (cam 6): YOLO da „person" fals pe o fasie IR de gard, mereu in aceleasi patratele.
Reantrenarea nu ajuta (decupaj prea mic). Solutia: tinem minte UNDE si CUM ARATA falsul, din
verdictele pe care omul deja le da, si suprimam alarma acolo — fara sa atingem ce e nou sau
real. Memoria traieste in PostgreSQL + pgvector (tabelul `vectori_fp`).

Scor cu doi termeni:

    scor = poz + W_EMB * max(0, sim_la_false - sim_la_reale)
      poz          = fals / (fals + ok + K) in patratel + eticheta + regim (zi/noapte)
      sim_la_false = cosinus intre embedding-ul SigLIP al crop-ului si cel mai apropiat „gresit”
      sim_la_reale = idem fata de cel mai apropiat „ok”

Suprima alarma DOAR daca: fals >= MIN_FALS si scor >= PRAG si ok == 0 in patratel (plasa de
siguranta: daca acolo a fost vreodata OM/obiect real confirmat, nu suprimam — mutul se
dizolva singur cand omul da un „ok”). Embedding-ul e optional: fara el, scorul e pur
pozitional (destul pentru cam 6). Vectorii se trimit ca text `'[...]'::vector`, deci nu
depind de inregistrarea tipului pe conexiune.
"""
import json
import time

import db

DIM = 768                 # SigLIP base multilingual
GRILA = (24, 32)          # (randuri, coloane) peste cadru — ~32 px la 1024x768
K = 0.5                   # netezire a ratei de fals (ales ca MIN_FALS falsuri + 0 ok sa treaca PRAG)
MIN_FALS = 3              # cate verdicte „gresit” in patratel inainte sa putem suprima
PRAG = 0.8                # pragul scorului pentru suprimare (poz la 3 fals / 0 ok = 0.857)
W_EMB = 0.5               # cat cantareste termenul semantic (0 = pur pozitional)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS vectori_fp (
    id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cam      INTEGER NOT NULL,
    eticheta TEXT    NOT NULL,
    regim    TEXT    NOT NULL,
    cel_r    INTEGER NOT NULL,
    cel_c    INTEGER NOT NULL,
    verdict  TEXT    NOT NULL,
    box      JSONB,
    poza     TEXT,
    ts       DOUBLE PRECISION NOT NULL,
    emb      vector({DIM})
);
CREATE INDEX IF NOT EXISTS vfp_cheie ON vectori_fp (cam, eticheta, regim, cel_r, cel_c);
CREATE INDEX IF NOT EXISTS vfp_hnsw ON vectori_fp USING hnsw (emb vector_cosine_ops);
"""
MIGRARI = {}
_gata = False


def _init():
    global _gata
    if not _gata:
        db.aplica("scorer_fp", SCHEMA, MIGRARI)
        _gata = True


def reset():
    global _gata
    _gata = False


def regim_din_ora(ora):
    """Zi/noapte dupa ora locala (noaptea: fenomenul IR al falsurilor)."""
    return "noapte" if (ora < 7 or ora >= 19) else "zi"


def celula(box):
    """Patratelul (rand, col) al centrului unui box normalizat [x, y, w, h]."""
    x, y, w, h = box
    rr, cc = GRILA
    r = min(rr - 1, max(0, int((y + h / 2) * rr)))
    c = min(cc - 1, max(0, int((x + w / 2) * cc)))
    return r, c


def _vec(emb):
    """Embedding (iterabil de float) -> literalul PostgreSQL '[a,b,c]'; None ramane None."""
    if emb is None:
        return None
    return "[" + ",".join(f"{float(v):.6f}" for v in emb) + "]"


def adauga(cam, eticheta, regim, box, verdict, emb=None, poza=None, ts=None):
    """Memoreaza un verdict (un punct de date pentru scor). `verdict` in {'gresit','ok'}."""
    _init()
    r, c = celula(box)
    with db.conn() as cn:
        cn.execute("INSERT INTO vectori_fp (cam, eticheta, regim, cel_r, cel_c, verdict, box, poza, ts, emb) "
                   "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s::vector)",
                   (int(cam), eticheta, regim, r, c, verdict, json.dumps(list(box)), poza,
                    ts if ts is not None else time.time(), _vec(emb)))


def _numara(cn, cam, eticheta, regim, r, c):
    row = cn.execute(
        "SELECT count(*) FILTER (WHERE verdict='gresit'), count(*) FILTER (WHERE verdict='ok') "
        "FROM vectori_fp WHERE cam=%s AND eticheta=%s AND regim=%s AND cel_r=%s AND cel_c=%s",
        (int(cam), eticheta, regim, r, c)).fetchone()
    return int(row[0]), int(row[1])


def _sim(cn, cam, eticheta, verdict, vec):
    row = cn.execute(
        "SELECT 1 - (emb <=> %s::vector) FROM vectori_fp "
        "WHERE cam=%s AND eticheta=%s AND verdict=%s AND emb IS NOT NULL "
        "ORDER BY emb <=> %s::vector LIMIT 1",
        (vec, int(cam), eticheta, verdict, vec)).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def scor(cam, eticheta, regim, box, emb=None):
    """Intoarce {scor, fals, ok, poz, emb_term, suprima} pentru o detectie."""
    _init()
    r, c = celula(box)
    vec = _vec(emb)
    with db.conn() as cn:
        fals, ok = _numara(cn, cam, eticheta, regim, r, c)
        emb_term = 0.0
        if vec is not None and fals:
            sf = _sim(cn, cam, eticheta, "gresit", vec)
            so = _sim(cn, cam, eticheta, "ok", vec)
            if sf is not None:
                emb_term = max(0.0, sf - (so if so is not None else 0.0))
    poz = fals / (fals + ok + K)
    s = poz + W_EMB * emb_term
    suprima = ok == 0 and fals >= MIN_FALS and s >= PRAG
    return {"scor": round(s, 4), "fals": fals, "ok": ok, "poz": round(poz, 4),
            "emb_term": round(emb_term, 4), "suprima": suprima}


def suprima(cam, eticheta, regim, box, emb=None):
    """True daca detectia cade intr-un patratel „invatat ca fals” (vezi scor())."""
    return scor(cam, eticheta, regim, box, emb)["suprima"]


def incarca_din_feedback(cale, embed_fn=None, doar_cam=None):
    """Populeaza memoria din data/scena/feedback.jsonl (verdictele pe detectii). `embed_fn(poza)`
    -> embedding SigLIP sau None (optional; fara el memoria e pur pozitionala). Idempotent prin
    golire + reincarcare pentru camerele atinse. Intoarce cate randuri a scris."""
    import json
    _init()
    randuri = []
    for ln in open(cale, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        cam = e.get("cam")
        verdict = e.get("verdict")
        box = e.get("regiune")
        eticheta = e.get("eticheta_initiala") or e.get("eticheta")
        if cam is None or verdict not in ("gresit", "ok") or not box or not eticheta:
            continue
        if doar_cam is not None and int(cam) != int(doar_cam):
            continue
        ora = int((e.get("t") or "1970-01-01 12")[11:13] or 12)
        randuri.append((int(cam), eticheta, regim_din_ora(ora), box, verdict,
                        e.get("poza"), embed_fn(e.get("poza")) if embed_fn else None))
    cams = {r[0] for r in randuri}
    with db.conn() as cn:
        for cam in cams:
            cn.execute("DELETE FROM vectori_fp WHERE cam=%s", (cam,))
    for cam, eticheta, regim, box, verdict, poza, emb in randuri:
        adauga(cam, eticheta, regim, box, verdict, emb=emb, poza=poza)
    return len(randuri)
