"""Jurnalul SQLite (app/jurnal.py): scriere, filtre, zi locala, retentie, migrare."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import jurnal  # noqa: E402
import rules  # noqa: E402


def _ev(t, tip="detectie", cam=1, **extra):
    jurnal.insert({"t": t, "tip": tip, "cam": cam, **extra})


def test_insert_si_query_intorc_forma_plata():
    jurnal.insert({"id": "abc", "t": "2026-10-20 10:00:00", "tip": "numar", "sev": "atentie",
                   "cam": 3, "numar": "B123XYZ", "nume": None})
    e = jurnal.query()[0]
    assert e == {"id": "abc", "t": "2026-10-20 10:00:00", "tip": "numar", "sev": "atentie",
                 "cam": 3, "numar": "B123XYZ", "nume": None,
                 "ts": jurnal._epoch("2026-10-20 10:00:00")}


def test_fara_camera_nu_are_cheia_cam():
    jurnal.insert({"t": "2026-10-20 10:00:00", "tip": "camera_offline"})
    e = jurnal.query()[0]
    assert "cam" not in e and e["sev"] == "info" and len(e["id"]) == 12


def test_ordinea_cele_mai_noi_primele_si_limit():
    for i in range(10):
        _ev(f"2026-10-20 10:00:{i:02d}", n=i)
    out = jurnal.query(limit=3)
    assert [e["n"] for e in out] == [9, 8, 7]


def test_aceeasi_secunda_pastreaza_ordinea_inserarii():
    for i in range(5):
        _ev("2026-10-20 10:00:00", n=i)
    assert [e["n"] for e in jurnal.query()] == [4, 3, 2, 1, 0]


def test_filtre_tip_cam_sev():
    _ev("2026-10-20 10:00:00", tip="detectie", cam=1, sev="info")
    _ev("2026-10-20 10:00:01", tip="zgomot", cam=1, sev="atentie")
    _ev("2026-10-20 10:00:02", tip="persoana_necunoscuta", cam=2, sev="critic")
    assert len(jurnal.query(tip="detectie")) == 1
    assert len(jurnal.query(cam=1)) == 2
    assert len(jurnal.query(tip="zgomot", cam=1)) == 1
    assert len(jurnal.query(cam=99)) == 0
    assert [e["tip"] for e in jurnal.query(min_sev="atentie")] == \
        ["persoana_necunoscuta", "zgomot"]
    assert [e["tip"] for e in jurnal.query(min_sev="critic")] == ["persoana_necunoscuta"]
    assert len(jurnal.query(min_sev="info")) == 3


def test_filtrul_pe_zi_e_ziua_locala_intreaga():
    _ev("2026-10-19 23:59:59", n=1)
    _ev("2026-10-20 00:00:00", n=2)
    _ev("2026-10-20 23:59:59", n=3)
    _ev("2026-10-21 00:00:00", n=4)
    assert sorted(e["n"] for e in jurnal.query(day="2026-10-20")) == [2, 3]
    assert jurnal.query(day="1999-01-01") == []
    assert jurnal.query(day="nu-e-zi") == []


def test_since_until_in_epoch():
    _ev("2026-10-20 10:00:00", n=1)
    _ev("2026-10-20 11:00:00", n=2)
    _ev("2026-10-20 12:00:00", n=3)
    t11 = jurnal._epoch("2026-10-20 11:00:00")
    assert [e["n"] for e in jurnal.query(since=t11)] == [3, 2]
    assert [e["n"] for e in jurnal.query(until=t11)] == [1]
    assert [e["n"] for e in jurnal.query(since=t11, until=t11 + 1)] == [2]


def test_id_duplicat_se_ignora():
    jurnal.insert({"id": "x", "t": "2026-10-20 10:00:00", "tip": "a"})
    jurnal.insert({"id": "x", "t": "2026-10-20 10:00:01", "tip": "b"})
    assert len(jurnal.query()) == 1 and jurnal.query()[0]["tip"] == "a"


def test_trim_sterge_dupa_varsta():
    acum = time.time()
    vechi = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(acum - 40 * 86400))
    nou = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(acum - 86400))
    _ev(vechi, n="vechi")
    _ev(nou, n="nou")
    assert jurnal.trim(keep_days=30) == 1
    assert [e["n"] for e in jurnal.query()] == ["nou"]


def test_persista_dupa_reset():
    _ev("2026-10-20 10:00:00", n=1)
    jurnal.reset()
    assert jurnal.query()[0]["n"] == 1


def test_stats():
    _ev("2026-10-20 10:00:00")
    _ev("2026-10-21 10:00:00")
    s = jurnal.stats()
    assert s["evenimente"] == 2 and s["cel_mai_vechi"] == "2026-10-20 10:00:00" \
        and s["cel_mai_nou"] == "2026-10-21 10:00:00" and s["mb"] >= 0


def test_migrare_din_jsonl():
    linii = [json.dumps({"id": "a1", "t": "2026-09-22 20:22:53", "tip": "alarma_camera",
                         "sev": "info", "cam": 10, "subtip": "fielddetection"}),
             json.dumps({"t": "2026-09-22 20:23:00", "tip": "detectie", "cam": 5,
                         "eticheta": "dog"}),                # fara id -> generat
             "{gunoi",                                       # linie stricata -> sarita
             json.dumps({"tip": "fara_timp"}),               # fara t -> sarita
             json.dumps({"id": "a3", "t": "2026-09-22 20:24:00", "tip": "zgomot", "cam": 5})]
    with open(jurnal.JSONL_FILE, "w") as f:
        f.write("\n".join(linii) + "\n")
    jurnal.deschide()
    out = jurnal.query()
    assert [e["tip"] for e in out] == ["zgomot", "detectie", "alarma_camera"]
    assert out[2]["id"] == "a1" and out[2]["subtip"] == "fielddetection"
    assert len(out[1]["id"]) == 12
    assert not os.path.exists(jurnal.JSONL_FILE)
    assert os.path.exists(jurnal.JSONL_FILE + ".migrat")
    # a doua deschidere nu re-importa nimic (fisierul a fost mutat, baza are randuri)
    jurnal.reset()
    jurnal.deschide()
    assert len(jurnal.query()) == 3


def test_deschiderea_lenesa_nu_migreaza():
    with open(jurnal.JSONL_FILE, "w") as f:
        f.write(json.dumps({"t": "2026-10-20 11:00:00", "tip": "x"}) + "\n")
    assert jurnal.query() == []                   # doar cititor (nvr-ai): nu importa
    assert os.path.exists(jurnal.JSONL_FILE)


def test_migrarea_nu_ruleaza_peste_o_baza_cu_date():
    _ev("2026-10-20 10:00:00")
    with open(jurnal.JSONL_FILE, "w") as f:
        f.write(json.dumps({"t": "2026-10-20 11:00:00", "tip": "x"}) + "\n")
    jurnal.reset()
    jurnal.deschide()
    assert len(jurnal.query()) == 1
    assert os.path.exists(jurnal.JSONL_FILE)      # neatins


def test_rules_journal_scrie_si_citeste_prin_sqlite(monkeypatch):
    monkeypatch.setattr(rules, "_is_night", lambda *a: False)
    rules._journal("detectie", 7, {"eticheta": "cat", "tip": "nu-suprascrie", "id": "nici"})
    e = rules.journal()[0]
    assert e["tip"] == "detectie" and e["cam"] == 7 and e["eticheta"] == "cat"
    assert e["subtip"] == "nu-suprascrie" and e["ev_id"] == "nici" and e["sev"] == "info"
    assert jurnal.query(cam=7)[0]["id"] == e["id"]


def test_multe_evenimente_raman_rapide():
    base = jurnal._epoch("2026-10-01 00:00:00")
    c = jurnal._connect()
    with jurnal._lock:
        c.execute("BEGIN")
        c.executemany(jurnal.INSERT, [jurnal._row({"id": f"id{i}", "t": "2026-10-01 00:00:00",
                                                   "tip": "detectie" if i % 3 else "obiect",
                                                   "cam": i % 18}, ts=base + i)
                                      for i in range(30000)])
        c.execute("COMMIT")
    t0 = time.time()
    assert len(jurnal.query(limit=20000, tip="obiect")) == 10000
    assert len(jurnal.query(day="2026-10-01", limit=100000)) == 30000
    assert jurnal.query(cam=5, limit=3)[0]["id"] == "id29993"
    assert time.time() - t0 < 1.0
