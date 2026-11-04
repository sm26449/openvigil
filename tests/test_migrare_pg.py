"""Migrarea datelor SQLite -> PostgreSQL, Faza 1 (scripts/migrare_pg.py).

Construieste o baza SQLite cu schema VECHE (ca inainte de 2.0.0) + cate un rand pe tabel,
ruleaza migrarea si verifica prin API-ul modulelor (acum pe PG) ca datele au ajuns. Sare
daca PostgreSQL nu e disponibil.
"""
import os
import sqlite3
import sys

import pytest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "app"))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import db  # noqa: E402
import jobs  # noqa: E402
import jurnal  # noqa: E402
import marcaje  # noqa: E402
import push  # noqa: E402
import migrare_pg  # noqa: E402


@pytest.fixture(autouse=True)
def _pg():
    try:
        db.ping()
    except Exception as e:
        pytest.skip(f"PostgreSQL indisponibil: {e}")
    jobs._init()
    push._init()
    marcaje._init()
    with db.conn() as c:
        c.execute("TRUNCATE jobs, push_abonamente, marcaje")
    yield


def _sqlite_vechi(cale):
    """Baza SQLite cu schema de dinainte de 2.0.0 (fara seq, timpi pe INTEGER/REAL)."""
    c = sqlite3.connect(cale)
    c.executescript("""
        CREATE TABLE push_abonamente(id INTEGER PRIMARY KEY, user TEXT, endpoint TEXT UNIQUE,
            p256dh TEXT, auth TEXT, nivel TEXT, agent TEXT, creat REAL, ultima REAL, erori INTEGER);
        CREATE TABLE jobs(id TEXT PRIMARY KEY, tip TEXT, stare TEXT, progres INTEGER, mesaj TEXT,
            parametri TEXT, rezultat TEXT, fisier TEXT, nume_fisier TEXT, mime TEXT, creat_de TEXT,
            cam INTEGER, creat INTEGER, pornit INTEGER, terminat INTEGER);
        CREATE TABLE marcaje(id TEXT PRIMARY KEY, cam INTEGER, ts INTEGER, ts_end INTEGER, t TEXT,
            t_end TEXT, eticheta TEXT, nota TEXT, creat_de TEXT, creat TEXT);
        CREATE TABLE events(rowid INTEGER PRIMARY KEY, id TEXT UNIQUE, ts INTEGER, t TEXT,
            tip TEXT, sev TEXT, cam INTEGER, date TEXT);
    """)
    c.execute("INSERT INTO events(id,ts,t,tip,sev,cam,date) "
              "VALUES('e1',100,'2026-10-20 10:00:00','detectie','info',5,'{\"eticheta\":\"dog\"}')")
    c.execute("INSERT INTO events(id,ts,t,tip,sev,cam,date) "
              "VALUES('e2',200,'2026-10-20 10:05:00','zgomot','atentie',7,'{}')")
    c.execute("INSERT INTO push_abonamente(user,endpoint,p256dh,auth,nivel,agent,creat,ultima,erori) "
              "VALUES('admin','https://push.example/x','P','A','critic','Chrome',1.0,NULL,0)")
    c.execute("INSERT INTO jobs(id,tip,stare,progres,mesaj,parametri,creat_de,creat) "
              "VALUES('job1','export','gata',100,'','{}','admin',1000)")
    c.execute("INSERT INTO marcaje(id,cam,ts,ts_end,t,t_end,eticheta,nota,creat_de,creat) "
              "VALUES('m1',5,100,100,'2026-10-23 10:00:00','2026-10-23 10:00:00','x','','admin','2026-10-23 10:00:00')")
    c.commit()
    c.close()


def test_migrare_faza1_si_idempotenta(tmp_path):
    ev = str(tmp_path / "events.db")
    _sqlite_vechi(ev)

    rez = migrare_pg.migreaza(ev)
    assert rez == {"push_abonamente": 1, "jobs": 1, "marcaje": 1, "events": 2}

    # datele au ajuns in PG, citite prin API-ul modulelor (acum pe PostgreSQL)
    lst = push.lista()
    assert len(lst) == 1 and lst[0]["user"] == "admin" and lst[0]["nivel"] == "critic"
    assert jobs.get("job1")["stare"] == "gata" and jobs.get("job1")["creat_de"] == "admin"
    assert marcaje.get("m1")["cam"] == 5 and marcaje.get("m1")["eticheta"] == "x"
    assert len(jurnal.query()) == 2 and jurnal.query(cam=5)[0]["eticheta"] == "dog"

    # a doua rulare nu mai adauga nimic (ON CONFLICT DO NOTHING)
    assert migrare_pg.migreaza(ev) == {"push_abonamente": 0, "jobs": 0, "marcaje": 0, "events": 0}


def test_baza_lipsa_nu_arunca(tmp_path):
    assert migrare_pg.migreaza(str(tmp_path / "nu-exista.db")) == {}
