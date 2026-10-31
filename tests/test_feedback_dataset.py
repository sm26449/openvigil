"""Verdictele din „Invata scena” -> dataset (scripts/feedback_dataset.py): ultimul verdict
conteaza, etichetele neutre devin fundal, numele rare raman afara, clasele isi pastreaza
id-urile, impartirea train/val e fixa; si comanda jobului (app/antrenare.py)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import feedback_dataset as fd  # noqa: E402
import antrenare  # noqa: E402

EN = {"om": "person", "copil": "child", "tractor": "tractor", "umbră": "shadow", "cal cu căruță": "horse and cart"}


def test_verdicte_ultimul_conteaza(tmp_path):
    f = tmp_path / "feedback.jsonl"
    f.write_text('{"id":"a","verdict":"ok"}\nnu e json\n{"id":"a","verdict":"gresit","eticheta":"om"}\n{"id":"b","verdict":"necunoscut"}\n')
    v = fd.verdicte(str(f))
    assert v["a"]["verdict"] == "gresit" and set(v) == {"a", "b"}
    assert fd.verdicte(str(tmp_path / "lipsa.jsonl")) == {}


def test_eticheta_si_clasa():
    assert fd.eticheta_finala({"verdict": "ok", "eticheta_initiala": "om"}) == "om"
    assert fd.eticheta_finala({"verdict": "ok", "eticheta": "copil", "eticheta_initiala": "om"}) == "copil"
    assert fd.eticheta_finala({"verdict": "gresit", "eticheta": "tractor"}) == "tractor"
    assert fd.eticheta_finala({"verdict": "gresit"}) is None
    assert fd.eticheta_finala({"verdict": "necunoscut", "eticheta": "om"}) is None
    eng = lambda n: EN.get(n, n)  # noqa: E731
    assert fd.clasa_pentru("om", eng) == ("person", False)
    assert fd.clasa_pentru("copil", eng) == ("person", False)          # alias
    assert fd.clasa_pentru("umbră", eng) == (None, True)               # fundal
    assert fd.clasa_pentru("cal cu căruță", eng) == ("horse and cart", False)
    assert fd.clasa_pentru(None, eng) == (None, False)


def test_split_fix_si_chenar_implicit():
    assert fd.split_pentru("20261031-1.jpg") == fd.split_pentru("20261031-1.jpg")
    n = sum(fd.split_pentru(f"p{i}.jpg") == "val" for i in range(2000))
    assert 200 < n < 400                      # ~15%
    cx, cy, w, h = fd.chenar_implicit(300, 200)
    assert (cx, cy) == (0.5, 0.5) and 0.6 < w < 0.7 and w == h


def test_clase_persistente(tmp_path, monkeypatch):
    monkeypatch.setattr(fd, "CLASE_FN", str(tmp_path / "clase.json"))
    c = fd.clase_persistente(["tractor"])
    assert c[:11] == fd.CLASSES and c[11] == "tractor"
    (tmp_path / "clase.json").write_text(json.dumps(c))
    c2 = fd.clase_persistente(["horse and cart", "tractor"])
    assert c2.index("tractor") == 11 and c2.index("horse and cart") == 12   # id-urile vechi raman


def test_main_scrie_datasetul(tmp_path, monkeypatch):
    import numpy as np
    cv2 = pytest.importorskip("cv2")
    out, scena = tmp_path / "finetune", tmp_path / "scena"
    monkeypatch.setattr(fd, "OUT", str(out))
    monkeypatch.setattr(fd, "SCENA", str(scena))
    monkeypatch.setattr(fd, "CLASE_FN", str(out / "clase.json"))
    monkeypatch.setattr(fd, "MIN_POZITIVE", 2)
    (scena / "14").mkdir(parents=True)
    for i in range(6):
        cv2.imwrite(str(scena / "14" / f"p{i}.jpg"), np.full((120, 160, 3), 90, np.uint8))
    rows = [{"id": "a", "cam": 14, "poza": "p0.jpg", "verdict": "ok", "eticheta_initiala": "om"},
            {"id": "b", "cam": 14, "poza": "p1.jpg", "verdict": "gresit", "eticheta": "umbră"},
            {"id": "c", "cam": 14, "poza": "p2.jpg", "verdict": "gresit", "eticheta": "tractor"},
            {"id": "d", "cam": 14, "poza": "p3.jpg", "verdict": "gresit", "eticheta": "tractor"},
            {"id": "e", "cam": 14, "poza": "p4.jpg", "verdict": "gresit", "eticheta": "cal cu căruță"},   # rar
            {"id": "f", "cam": 14, "poza": "p5.jpg", "verdict": "necunoscut"},
            {"id": "g", "cam": 14, "poza": "lipsa.jpg", "verdict": "ok", "eticheta_initiala": "om"}]
    scena.mkdir(exist_ok=True)
    (scena / "feedback.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    import scena_clasa
    monkeypatch.setattr(scena_clasa, "engleza", lambda n: EN.get(n, n))
    # un fisier fb- vechi trebuie sa dispara
    (out / "images" / "train").mkdir(parents=True)
    (out / "images" / "train" / "fb-vechi.jpg").write_bytes(b"x")
    total = fd.main(folosi_yolo_world=False)
    assert total == 4                                   # om, umbra(fundal), tractor x2; rarul si necunoscutul nu
    assert not (out / "images" / "train" / "fb-vechi.jpg").exists()
    clase = json.load(open(out / "clase.json"))
    assert clase[11] == "tractor" and "horse and cart" not in clase
    etichete = {}
    for sp in ("train", "val"):
        for fn in os.listdir(out / "labels" / sp):
            etichete[fn] = (out / "labels" / sp / fn).read_text()
    assert etichete["fb-14-p1.txt"] == ""                             # fundal
    assert etichete["fb-14-p0.txt"].startswith("0 0.5")               # om = clasa 0, chenar din mijloc
    assert etichete["fb-14-p2.txt"].startswith("11 ")                 # tractor = clasa noua
    assert "11: tractor" in (out / "dataset.yaml").read_text()


def test_comanda_jobului():
    cmd, nume = antrenare._comanda("feedback")
    assert cmd[-1].endswith("feedback_dataset.py") and nume is None
    cmd, nume = antrenare._comanda("antrenare", epoci=60, baza="l")
    assert cmd[-1] == "l" and "--epoci" in cmd and nume.startswith("curte-v")
    cmd, _ = antrenare._comanda("antrenare", epoci=5, baza="xxl")
    assert cmd[-1] == "m"                                             # marime necunoscuta -> m
