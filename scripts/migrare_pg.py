#!/usr/bin/env python3
"""Migrarea datelor din SQLite in PostgreSQL (2.0.0), o singura data la cutover.

Faza 1: `push_abonamente`, `jobs`, `marcaje` din data/events.db -> PostgreSQL. Citeste din
SQLite si scrie in PG (prin app/db.py), pastrand id-urile si ordinea de inserare (`seq`-ul
joburilor urmeaza `rowid`-ul vechi). Ruleaza DUPA ce serviciul `db` e pornit si .env are
PG_*. Idempotent (`ON CONFLICT DO NOTHING`), deci se poate relua fara sa dubleze.

    python scripts/migrare_pg.py --events data/events.db
"""
import argparse
import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db  # noqa: E402
import jobs as _jobs  # noqa: E402
import jurnal as _jurnal  # noqa: E402
import lant as _lant  # noqa: E402
import marcaje as _marcaje  # noqa: E402
import push as _push  # noqa: E402


def _sqlite(cale):
    c = sqlite3.connect(cale)
    c.row_factory = sqlite3.Row
    return c


def _tabele(sc):
    return {r[0] for r in sc.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def migreaza_push(sc):
    _push._init()
    if "push_abonamente" not in _tabele(sc):
        return 0
    rows = sc.execute('SELECT "user", endpoint, p256dh, auth, nivel, agent, creat, ultima, erori '
                      "FROM push_abonamente ORDER BY id").fetchall()
    n = 0
    with db.conn() as c:
        for r in rows:
            cur = c.execute(
                'INSERT INTO push_abonamente("user", endpoint, p256dh, auth, nivel, agent, '
                "creat, ultima, erori) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(endpoint) DO NOTHING",
                (r["user"], r["endpoint"], r["p256dh"], r["auth"], r["nivel"],
                 r["agent"], r["creat"], r["ultima"], r["erori"]))
            n += cur.rowcount
    return n


def migreaza_jobs(sc):
    _jobs._init()
    if "jobs" not in _tabele(sc):
        return 0
    cols = ["id", "tip", "stare", "progres", "mesaj", "parametri", "rezultat", "fisier",
            "nume_fisier", "mime", "creat_de", "cam", "creat", "pornit", "terminat"]
    rows = sc.execute(f"SELECT {','.join(cols)} FROM jobs ORDER BY rowid").fetchall()
    ph = ",".join(["%s"] * len(cols))
    n = 0
    with db.conn() as c:
        for r in rows:
            cur = c.execute(f"INSERT INTO jobs ({','.join(cols)}) VALUES ({ph}) "
                            "ON CONFLICT (id) DO NOTHING", tuple(r[k] for k in cols))
            n += cur.rowcount
    return n


def migreaza_marcaje(sc):
    _marcaje._init()
    if "marcaje" not in _tabele(sc):
        return 0
    cols = ["id", "cam", "ts", "ts_end", "t", "t_end", "eticheta", "nota", "creat_de", "creat"]
    rows = sc.execute(f"SELECT {','.join(cols)} FROM marcaje ORDER BY ts").fetchall()
    ph = ",".join(["%s"] * len(cols))
    n = 0
    with db.conn() as c:
        for r in rows:
            cur = c.execute(f"INSERT INTO marcaje ({','.join(cols)}) VALUES ({ph}) "
                            "ON CONFLICT (id) DO NOTHING", tuple(r[k] for k in cols))
            n += cur.rowcount
    return n


def migreaza_events(sc):
    """Jurnalul: ~sute de mii de randuri, in loturi. Pastreaza id/ts/t/tip/sev/cam/date;
    `seq`-ul (IDENTITY) se atribuie in ordinea `rowid` veche."""
    _jurnal._init()
    if "events" not in _tabele(sc):
        return 0
    cols = ["id", "ts", "t", "tip", "sev", "cam", "date"]
    cur_s = sc.execute(f"SELECT {','.join(cols)} FROM events ORDER BY rowid")
    ph = ",".join(["%s"] * len(cols))
    sql = f"INSERT INTO events ({','.join(cols)}) VALUES ({ph}) ON CONFLICT (id) DO NOTHING"
    n = 0
    with db.conn() as c, c.cursor() as cur:
        while True:
            lot = cur_s.fetchmany(5000)
            if not lot:
                break
            cur.executemany(sql, [tuple(r[k] for k in cols) for r in lot])
            n += cur.rowcount if (cur.rowcount or -1) >= 0 else 0
    return n


def migreaza_segmente(sc):
    """Lantul de custodie. Pastreaza (cam, nume, epoch, cale, octeti, sha256, calculat, arhivat)."""
    _lant._init()
    if "segmente" not in _tabele(sc):
        return 0
    cols = ["cam", "nume", "epoch", "cale", "octeti", "sha256", "calculat", "arhivat"]
    rows = sc.execute(f"SELECT {','.join(cols)} FROM segmente ORDER BY cam, epoch").fetchall()
    ph = ",".join(["%s"] * len(cols))
    n = 0
    with db.conn() as c:
        for r in rows:
            cur = c.execute(f"INSERT INTO segmente ({','.join(cols)}) VALUES ({ph}) "
                            "ON CONFLICT (cam, nume) DO NOTHING", tuple(r[k] for k in cols))
            n += cur.rowcount
    return n


def verifica_segmente(sc):
    """Confirma ca fiecare amprenta SHA-256 a ajuns NESCHIMBATA in PostgreSQL — lantul de
    custodie trebuie sa ramana valabil peste migrare. Returneaza (verificate, nepotriviri).
    (Re-hash-ul fisierelor de pe disc ramane disponibil oricand prin lant.verifica / fisa.)"""
    if "segmente" not in _tabele(sc):
        return (0, 0)
    src = [(r["cam"], r["nume"], r["sha256"])
           for r in sc.execute("SELECT cam, nume, sha256 FROM segmente")]
    with db.conn() as c:
        dst = {(r[0], r[1]): r[2]
               for r in c.execute("SELECT cam, nume, sha256 FROM segmente").fetchall()}
    nepot = sum(1 for cam, nume, sha in src if dst.get((cam, nume)) != sha)
    return (len(src), nepot)


def migreaza(events):
    """Migreaza tabelele din baza SQLite `events`. Intoarce {tabel: nr_randuri noi}."""
    if not os.path.exists(events):
        print(f"[migrare] {events} nu exista — nimic de migrat")
        return {}
    sc = _sqlite(events)
    try:
        rez = {"push_abonamente": migreaza_push(sc),
               "jobs": migreaza_jobs(sc),
               "marcaje": migreaza_marcaje(sc),
               "events": migreaza_events(sc),
               "segmente": migreaza_segmente(sc)}
        ver, nepot = verifica_segmente(sc)
    finally:
        sc.close()
    if ver:
        print(f"[migrare] lant de custodie: {ver} segmente verificate, {nepot} nepotriviri de hash")
        if nepot:
            print("[migrare] ATENTIE: lantul are nepotriviri — investigheaza INAINTE de cutover")
    for tabel, n in rez.items():
        print(f"[migrare] {tabel}: {n} randuri noi in PostgreSQL")
    return rez


def main():
    ap = argparse.ArgumentParser(description="Migrare SQLite -> PostgreSQL (Faza 1)")
    ap.add_argument("--events", default=os.path.join(
        os.path.dirname(__file__), "..", "data", "events.db"),
        help="calea bazei SQLite events.db")
    args = ap.parse_args()
    migreaza(os.path.abspath(args.events))


if __name__ == "__main__":
    main()
