"""Copia nocturna criptata (app/backup_nocturn.py): creare -> refacere -> comparare (testul de
restaurare), cheie gresita, rotatie, trimitere externa (rsync fals / director)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import backup_nocturn as bn  # noqa: E402
import db  # noqa: E402
import jurnal  # noqa: E402
import settings  # noqa: E402


@pytest.fixture
def mediu(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "cameras.json").write_text('[{"id": 1, "name": "Poarta", "pass": "secret"}]')
    (data / "settings.json").write_text('{"a": 1}')
    (data / "secret_key").write_bytes(b"\x01" * 32)
    (data / "plates").mkdir(); (data / "plates" / "known.json").write_text("{}")
    (data / "faces" / "persons" / "ana").mkdir(parents=True)
    (data / "faces" / "persons" / "ana" / "1.jpg").write_bytes(b"jpg")
    (data / "faces" / "insightface").mkdir(); (data / "faces" / "insightface" / "model.onnx").write_bytes(b"model")
    monkeypatch.setattr(bn, "DATA", str(data))
    monkeypatch.setattr(bn, "KEY_FILE", str(tmp_path / "backup_key"))
    monkeypatch.setattr(bn, "STARE_FILE", str(tmp_path / "stare.json"))
    cfg = {"backup_dir": str(tmp_path / "out"), "backup_remote": "", "backup_pastreaza": 2, "backup_fete": True}
    monkeypatch.setattr(settings, "get", lambda k: cfg.get(k))
    monkeypatch.setattr(settings, "load", lambda: dict(cfg))
    return data, tmp_path / "out", cfg


def test_creare_si_refacere(mediu, tmp_path):
    data, out, cfg = mediu
    if not _pg_ok():
        pytest.skip("PostgreSQL indisponibil")
    jurnal.insert({"id": "evb1", "t": "2026-10-20 10:00:00", "tip": "detectie", "cam": 5})
    k = bn.cheie()
    assert len(k) == 44 and bn.cheie() == k and oct(os.stat(bn.KEY_FILE).st_mode)[-3:] == "600"
    m = bn.creeaza(data_dir=str(data))
    f = out / m["fisier"]
    assert f.exists() and m["cheie"] == bn.amprenta_cheie() and oct(os.stat(f).st_mode)[-3:] == "600"
    # baza e in PostgreSQL -> in arhiva ca pg/<tabel>.csv, nu ca events.db
    assert "cameras.json" in m["continut"] and "pg/events.csv" in m["continut"] and "faces/persons" in m["continut"]
    assert b"secret" not in f.read_bytes()                      # criptat: parola nu apare in clar
    manifest = json.load(open(str(f)[:-len(".tar.gz.enc")] + ".json"))
    dest = tmp_path / "refacut"
    fis = bn.restaureaza(str(f), k, str(dest), manifest)
    assert (dest / "cameras.json").read_text() == (data / "cameras.json").read_text()
    assert (dest / "faces" / "persons" / "ana" / "1.jpg").read_bytes() == b"jpg"
    assert not (dest / "faces" / "insightface").exists()         # modelele descarcabile nu intra
    assert b"evb1" in (dest / "pg" / "events.csv").read_bytes()  # dump-ul PG contine evenimentul
    assert "cameras.json" in fis and "secret_key" in fis
    assert manifest.get("complet") and manifest.get("pg_randuri", {}).get("events") == 1
    # cheie gresita si continut neconform cu manifestul -> ValueError (nu ascunse)
    with pytest.raises(ValueError):
        bn.restaureaza(str(f), "A" * 43 + "=", str(tmp_path / "x"))
    with pytest.raises(ValueError):
        bn.restaureaza(str(f), k, str(tmp_path / "y"), {"sha256_clar": "0" * 64})


def test_incarca_pg_reface_baza(mediu, tmp_path):
    """Round-trip PostgreSQL: dump -> TRUNCATE -> incarca_pg reface randurile."""
    if not _pg_ok():
        pytest.skip("PostgreSQL indisponibil")
    jurnal.insert({"id": "rt1", "t": "2026-10-20 10:00:00", "tip": "zgomot", "cam": 7})
    pg = tmp_path / "pg"
    pg.mkdir()
    with db.conn() as c:
        cols = bn._coloane_ne_identity(c, "events")
    lista = ", ".join('"%s"' % x for x in cols)
    with db.conn() as c, c.cursor() as cur, cur.copy(
            f'COPY "events" ({lista}) TO STDOUT (FORMAT csv, HEADER)') as cp:
        (pg / "events.csv").write_bytes(b"".join(cp))
    with db.conn() as c:
        c.execute("TRUNCATE events")
    assert jurnal.query() == []
    rez = bn.incarca_pg(str(tmp_path))
    assert rez["events"] == 1 and jurnal.query()[0]["id"] == "rt1"


def test_incarca_pg_atomic(mediu, tmp_path):
    """incarca_pg e atomic: un CSV invalid in lot face rollback, baza ramane neatinsa
    (nu lasa events golit de TRUNCATE)."""
    if not _pg_ok():
        pytest.skip("PostgreSQL indisponibil")
    jurnal.insert({"id": "atom1", "t": "2026-10-20 10:00:00", "tip": "detectie", "cam": 3})
    pg = tmp_path / "pg"
    pg.mkdir()
    with db.conn() as c:
        cols = bn._coloane_ne_identity(c, "events")
    lista = ", ".join('"%s"' % x for x in cols)
    with db.conn() as c, c.cursor() as cur, cur.copy(
            f'COPY "events" ({lista}) TO STDOUT (FORMAT csv, HEADER)') as cp:
        (pg / "events.csv").write_bytes(b"".join(cp))
    # al doilea fisier (tabel existent, dar coloana inexistenta) -> COPY esueaza la mijloc
    (pg / "push.csv").write_bytes(b'"coloana_inexistenta"\nx\n')
    with pytest.raises(Exception):
        bn.incarca_pg(str(tmp_path))
    # events NU a fost golit: TRUNCATE-ul a fost rulat inaintea COPY-ului esuat, dar tranzactia
    # a facut rollback
    assert jurnal.query()[0]["id"] == "atom1"


def _pg_ok():
    try:
        db.ping()
        return True
    except Exception:
        return False


def test_rotatie_lista_si_trimitere(mediu, tmp_path):
    data, out, cfg = mediu
    for _ in range(3):
        m = bn.creeaza(data_dir=str(data), fete=False)
        os.utime(out / m["fisier"])
        import time; time.sleep(1.1)
    assert len(bn.lista()) == 3
    assert bn.roteste(pastreaza=2) == 1 and len(bn.lista()) == 2
    ultima = bn.lista()[0]
    assert "faces/persons" not in ultima["continut"]
    # director montat
    ok, msg = bn.trimite(str(out / ultima["fisier"]), str(tmp_path / "extern"))
    assert ok and (tmp_path / "extern" / ultima["fisier"]).exists()
    # rsync fals
    vazut = {}
    class R: returncode = 0; stderr = ""
    def run_fals(cmd, **kw): vazut["cmd"] = cmd; return R()
    ok, msg = bn.trimite(str(out / ultima["fisier"]), "stefan@casa:/backup/nvr", run=run_fals)
    assert ok and vazut["cmd"][0] == "rsync" and vazut["cmd"][-1] == "stefan@casa:/backup/nvr/"
    assert bn.trimite(str(out / ultima["fisier"]), "") == (None, "fara destinatie externa")
    # ruleaza(): stare + manifest cu extern
    cfg["backup_remote"] = str(tmp_path / "extern2")
    m = bn.ruleaza(cine="test")
    assert m["extern"]["ok"] and bn.stare()["ok"] and bn.stare()["ultima"]["fisier"] == m["fisier"]
