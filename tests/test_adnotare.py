"""adnotare.py: clasele din dataset.yaml, listarea, citirea si scrierea etichetelor YOLO,
marcarea cadrelor verificate (pe un dataset mic, in tmp)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import adnotare  # noqa: E402


@pytest.fixture
def ds(tmp_path, monkeypatch):
    import numpy as np, cv2
    monkeypatch.setattr(adnotare, "FINETUNE", str(tmp_path))
    monkeypatch.setattr(adnotare, "VERIFICATE", str(tmp_path / "verificate.json"))
    (tmp_path / "dataset.yaml").write_text("path: x\ntrain: images/train\nval: images/val\nnames:\n  0: person\n  1: car\n  2: dog\n")
    for sp, fns in (("train", ["cam1-20260101-100000.jpg", "cam2-20260101-100100.jpg"]), ("val", ["cam1-20260102-100000.jpg"])):
        (tmp_path / "images" / sp).mkdir(parents=True); (tmp_path / "labels" / sp).mkdir(parents=True)
        for fn in fns:
            cv2.imwrite(str(tmp_path / "images" / sp / fn), np.zeros((36, 64, 3), np.uint8))
            (tmp_path / "labels" / sp / (fn[:-4] + ".txt")).write_text("")
    (tmp_path / "labels" / "train" / "cam1-20260101-100000.txt").write_text("0 0.5 0.5 0.2 0.4\n2 0.1 0.1 0.1 0.1\n")
    return tmp_path


def test_clase_si_lista(ds):
    assert adnotare.clase() == ["person", "car", "dog"]
    l = adnotare.lista()
    assert l["total"] == 3 and l["verificate"] == 0
    assert [c["fn"] for c in l["cadre"]][0] == "cam1-20260102-100000.jpg"     # cele mai noi primele (dupa data, nu dupa camera)
    assert {c["fn"]: c["n"] for c in l["cadre"]}["cam1-20260101-100000.jpg"] == 2
    assert adnotare.lista(split="val")["total"] == 1
    assert adnotare.lista(cam=2)["total"] == 1


def test_cadru_si_salvare(ds):
    c = adnotare.cadru("train", "cam1-20260101-100000.jpg")
    assert (c["w"], c["h"]) == (64, 36) and len(c["boxes"]) == 2 and c["boxes"][1]["cls"] == 2
    rez = adnotare.salveaza("train", "cam1-20260101-100000.jpg",
                            [{"cls": 1, "cx": 0.5, "cy": 0.5, "w": 0.3, "h": 0.3}], cine="admin")
    assert rez["ok"] and rez["n"] == 1
    assert adnotare.citeste_boxes(str(ds / "labels" / "train" / "cam1-20260101-100000.txt")) == \
        [{"cls": 1, "cx": 0.5, "cy": 0.5, "w": 0.3, "h": 0.3}]
    ver = json.loads((ds / "verificate.json").read_text())
    assert ver["cam1-20260101-100000.jpg"]["cine"] == "admin"
    assert adnotare.lista(doar_neverificate=True)["total"] == 2
    assert adnotare.cadru("train", "cam1-20260101-100000.jpg")["verificat"]["n"] == 1


def test_salvare_refuza_chenare_rele(ds):
    with pytest.raises(ValueError):
        adnotare.salveaza("train", "cam1-20260101-100000.jpg", [{"cls": 7, "cx": 0.5, "cy": 0.5, "w": 0.1, "h": 0.1}])
    with pytest.raises(ValueError):
        adnotare.salveaza("train", "cam1-20260101-100000.jpg", [{"cls": 0, "cx": 1.5, "cy": 0.5, "w": 0.1, "h": 0.1}])
    with pytest.raises(ValueError):
        adnotare.salveaza("train", "../x.jpg", [])
    with pytest.raises(FileNotFoundError):
        adnotare.salveaza("train", "nu-exista.jpg", [])
    # fara chenare = cadru de fundal, valid
    assert adnotare.salveaza("val", "cam1-20260102-100000.jpg", [])["n"] == 0


def test_coada_nesigure_intra_in_dataset(ds, tmp_path, monkeypatch):
    import nesigure
    import numpy as np, cv2
    brut = tmp_path / "brut"; brut.mkdir()
    monkeypatch.setattr(nesigure, "DATASET_DIR", str(brut))
    monkeypatch.setattr(nesigure, "FINETUNE", str(tmp_path))
    monkeypatch.setattr(nesigure, "FISIER", str(tmp_path / "nesigure.json"))
    for fn in ("cam3-20260110-120000.jpg", "cam3-20260111-120000.jpg", "cam1-20260101-100000.jpg"):
        cv2.imwrite(str(brut / fn), np.zeros((36, 64, 3), np.uint8))
    # candidatii: cele din brut care nu sunt deja in dataset
    assert nesigure.candidati(10) == ["cam3-20260111-120000.jpg", "cam3-20260110-120000.jpg"]
    nesigure._scrie({"cam3-20260110-120000.jpg": {"boxes": [{"cls": 2, "cx": .5, "cy": .5, "w": .2, "h": .2, "conf": .41}], "scor": 1},
                     "cam3-20260111-120000.jpg": {"boxes": [{"cls": 0, "cx": .5, "cy": .5, "w": .2, "h": .2, "conf": .35},
                                                             {"cls": 0, "cx": .2, "cy": .2, "w": .1, "h": .1, "conf": .5}], "scor": 2}})
    l = adnotare.lista(split="nesigure")
    assert l["total"] == 2 and l["cadre"][0]["fn"] == "cam3-20260111-120000.jpg" and l["cadre"][0]["scor"] == 2
    c = adnotare.cadru("nesigure", "cam3-20260110-120000.jpg")
    assert c["boxes"][0]["conf"] == .41 and (c["w"], c["h"]) == (64, 36)
    rez = adnotare.salveaza("nesigure", "cam3-20260110-120000.jpg", [{"cls": 2, "cx": .5, "cy": .5, "w": .2, "h": .2}], cine="admin")
    assert rez["ok"] and rez["n"] == 1
    assert (tmp_path / "images" / "train" / "cam3-20260110-120000.jpg").exists()
    assert (tmp_path / "labels" / "train" / "cam3-20260110-120000.txt").read_text().startswith("2 0.5")
    assert "cam3-20260110-120000.jpg" not in nesigure.incarca()
    assert nesigure.candidati(10) == []                       # unul in dataset, unul in coada
    assert nesigure.scoate("cam3-20260111-120000.jpg") and nesigure.incarca() == {}
    assert nesigure.scoate("nu-exista.jpg") is False
