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

# B2 — veto semantic: suprimarea ramane un SUBSET al candidatilor pozitionali; dovada ca detectia
# e REALA (seamana cu un exemplar "ok", sau NU seamana cu niciun fals invatat) BLOCHEAZA suprimarea.
# Invarianta: vetoul DOAR scoate suprimare, n-o adauga -> nu putem rata in plus un om real.
SIM_FALS_MIN = 0.6        # sub aceasta asemanare cu falsurile invatate -> crop NOU -> nu suprimam
MARJA_REAL = 0.05         # daca seamana mai mult cu un "ok" (real) decat cu un fals -> nu suprimam

# B3 — robustete in calea fierbinte: interogarea PG a scorerului rula sincron in lotul serial de
# detectie; un PG degradat intarzia detectia TUTUROR camerelor (pool ~30s, SQL fara timeout).
SCORER_TIMEOUT_MS = 500   # timeout scurt: PG lent -> fail-open (0,0 = nu suprima), nu blocheaza lotul
CACHE_TTL = 60.0          # (fals, ok) se schimba doar la reincarcarea feedbackului -> cache scurt
CB_PRAG = 3               # erori PG la rand inainte sa deschidem circuit breaker-ul
CB_RACIRE = 30.0          # cat ocolim PG dupa ce breaker-ul s-a deschis

_cache = {}               # (cam,eticheta,regim,r,c) -> (expira, fals, ok)
_cb = {"erori": 0, "pana_la": 0.0, "ultima": ""}

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
    _cache.clear()
    _cb["erori"] = 0
    _cb["pana_la"] = 0.0


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
    # celula s-a schimbat -> invalidam cache-ul ei, ca un „ok” proaspat sa ridice imediat mutul
    # (plasa de siguranta), nu peste CACHE_TTL (B3)
    _cache.pop((int(cam), eticheta, regim, int(r), int(c)), None)


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


def _numara_robust(cam, eticheta, regim, r, c):
    """(fals, ok) cu cache TTL + circuit breaker + timeout scurt (B3). La PG degradat intoarce
    (0, 0) — fail-open: 0 falsuri => nu se suprima nimic, alarma suna — fara sa blocheze lotul
    serial de detectie al tuturor camerelor."""
    now = time.time()
    cheie = (int(cam), eticheta, regim, int(r), int(c))
    hit = _cache.get(cheie)
    if hit and hit[0] > now:
        return hit[1], hit[2]
    if _cb["pana_la"] > now:
        return 0, 0                               # breaker deschis -> ocolim PG
    try:
        with db.conn() as cn:
            with cn.transaction():                # SET LOCAL se reseteaza la finalul tranzactiei
                cn.execute(f"SET LOCAL statement_timeout = {int(SCORER_TIMEOUT_MS)}")
                fals, ok = _numara(cn, cam, eticheta, regim, r, c)
        _cb["erori"] = 0
        _cache[cheie] = (now + CACHE_TTL, fals, ok)
        return fals, ok
    except Exception as e:
        _cb["erori"] += 1
        _cb["ultima"] = str(e)[:120]
        if _cb["erori"] >= CB_PRAG:
            _cb["pana_la"] = now + CB_RACIRE
        return 0, 0


def _sim_pereche(cam, eticheta, vec):
    """(sim_la_fals, sim_la_real) cu acelasi timeout/breaker; la eroare (None, None) -> fara
    influenta semantica (nici termen, nici veto)."""
    if _cb["pana_la"] > time.time():
        return None, None
    try:
        with db.conn() as cn:
            with cn.transaction():
                cn.execute(f"SET LOCAL statement_timeout = {int(SCORER_TIMEOUT_MS)}")
                return (_sim(cn, cam, eticheta, "gresit", vec),
                        _sim(cn, cam, eticheta, "ok", vec))
    except Exception:
        return None, None


def stare():
    """Metrica de degradare (healthz/overlay): marimea cache-ului si starea circuit breaker-ului."""
    return {"cache": len(_cache), "cb_erori": _cb["erori"],
            "cb_deschis": _cb["pana_la"] > time.time(), "cb_ultima": _cb["ultima"]}


def scor(cam, eticheta, regim, box, emb=None):
    """Intoarce {scor, fals, ok, poz, emb_term, suprima, veto} pentru o detectie.

    Suprimarea = candidat POZITIONAL (ok==0, fals>=MIN_FALS, poz>=PRAG) SI fara veto semantic.
    Termenul semantic nu mai ADAUGA suprimare (ca inainte, cand poz>=0.857>PRAG il facea
    irelevant); el poate doar BLOCA suprimarea cand crop-ul arata a real (B2)."""
    _init()
    r, c = celula(box)
    fals, ok = _numara_robust(cam, eticheta, regim, r, c)
    poz = fals / (fals + ok + K)
    cand = ok == 0 and fals >= MIN_FALS and poz >= PRAG
    emb_term = 0.0
    veto = False
    if emb is not None and fals:
        sf, so = _sim_pereche(cam, eticheta, _vec(emb))
        if sf is not None:
            emb_term = max(0.0, sf - (so if so is not None else 0.0))
            # dovada ca e REAL, nu falsul invatat -> veto (nu suprimam)
            if (so is not None and so - sf >= MARJA_REAL) or (sf < SIM_FALS_MIN):
                veto = True
    s = poz + W_EMB * emb_term
    suprima = cand and not veto
    return {"scor": round(s, 4), "fals": fals, "ok": ok, "poz": round(poz, 4),
            "emb_term": round(emb_term, 4), "suprima": suprima, "veto": veto}


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
    _cache.clear()   # memoria s-a schimbat -> invalidam cache-ul de numere (B3)
    return len(randuri)
