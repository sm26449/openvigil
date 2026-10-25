"""Copia nocturna criptata (app/backup_nocturn.py): creare -> refacere -> comparare (testul de
restaurare), cheie gresita, rotatie, trimitere externa (rsync fals / director)."""
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import backup_nocturn as bn  # noqa: E402
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
    c = sqlite3.connect(data / "events.db"); c.execute("create table events(id text)"); c.execute("insert into events values('e1')"); c.commit(); c.close()
    monkeypatch.setattr(bn, "DATA", str(data))
    monkeypatch.setattr(bn, "KEY_FILE", str(tmp_path / "backup_key"))
    monkeypatch.setattr(bn, "STARE_FILE", str(tmp_path / "stare.json"))
    cfg = {"backup_dir": str(tmp_path / "out"), "backup_remote": "", "backup_pastreaza": 2, "backup_fete": True}
    monkeypatch.setattr(settings, "get", lambda k: cfg.get(k))
    monkeypatch.setattr(settings, "load", lambda: dict(cfg))
    return data, tmp_path / "out", cfg


def test_creare_si_refacere(mediu, tmp_path):
    data, out, cfg = mediu
    k = bn.cheie()
    assert len(k) == 44 and bn.cheie() == k and oct(os.stat(bn.KEY_FILE).st_mode)[-3:] == "600"
    m = bn.creeaza(data_dir=str(data))
    f = out / m["fisier"]
    assert f.exists() and m["cheie"] == bn.amprenta_cheie() and oct(os.stat(f).st_mode)[-3:] == "600"
    assert "cameras.json" in m["continut"] and "events.db" in m["continut"] and "faces/persons" in m["continut"]
    assert b"secret" not in f.read_bytes()                      # criptat: parola nu apare in clar
    manifest = json.load(open(str(f)[:-len(".tar.gz.enc")] + ".json"))
    dest = tmp_path / "refacut"
    fis = bn.restaureaza(str(f), k, str(dest), manifest)
    assert (dest / "cameras.json").read_text() == (data / "cameras.json").read_text()
    assert (dest / "faces" / "persons" / "ana" / "1.jpg").read_bytes() == b"jpg"
    assert not (dest / "faces" / "insightface").exists()         # modelele descarcabile nu intra
    c = sqlite3.connect(dest / "events.db"); assert c.execute("select id from events").fetchone()[0] == "e1"; c.close()
    assert "cameras.json" in fis and "secret_key" in fis
    with pytest.raises(ValueError):
        bn.restaureaza(str(f), "A" * 43 + "=", str(tmp_path / "x"))
    with pytest.raises(ValueError):
        bn.restaureaza(str(f), k, str(tmp_path / "y"), {"sha256_clar": "0" * 64})


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
