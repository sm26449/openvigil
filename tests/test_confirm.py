"""Confirmarea temporala din detect.CamState — in special gruparea pasarilor.

Modelul clipeste intre chicken/pigeon/bird la ~50% incredere; pe etichete separate
confirmarea de 2 cadre trecea aleator (sute de cadre-gunoi). Grupate sub o cheie,
prezenta se confirma indiferent de aroma, iar schimbarea aromei nu e aparitie noua.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import detect  # noqa: E402


class _Box:
    def __init__(self, cls_id, conf, xyxy=(100, 100, 200, 200)):
        self.cls = np.array(cls_id)          # 0-dim, ca tensorii ultralytics
        self.conf = np.array(conf)
        self.xyxy = np.array([xyxy], dtype=float)


class _Res:
    def __init__(self, boxes):
        self.boxes = boxes

    def plot(self):
        return np.zeros((10, 10, 3), np.uint8)


@pytest.fixture
def stare(monkeypatch):
    """CamState izolat: fara efecte (poze/notificari/hooks) si fara joburi grele."""
    monkeypatch.setattr(detect, "CLASSES",
                        {0: "person", 1: "chicken", 2: "pigeon", 3: "car"})
    monkeypatch.setattr(detect.snaps, "save_dataset", lambda *a: None)
    monkeypatch.setattr(detect.snaps, "save_frame", lambda *a, **k: None)
    monkeypatch.setattr(detect.notify, "detection", lambda *a: None)
    monkeypatch.setattr(detect, "cam_cfg", lambda cid: {})
    monkeypatch.setattr(detect.faces, "enabled", lambda: False)
    monkeypatch.setattr(detect.plates, "enabled", lambda: False)
    monkeypatch.setattr(detect.describe, "enabled", lambda: False)
    monkeypatch.setattr(detect, "_moment", lambda: "zi")
    st = detect.CamState(999)
    st._maybe_siren = lambda fresh: None
    return st


IMG = np.zeros((360, 640, 3), np.uint8)


def _emise(monkeypatch):
    out = []
    import hooks
    monkeypatch.setattr(hooks, "emit",
                        lambda ev, cam=None, **d: out.append(d.get("eticheta_ro")))
    return out


def test_pasarea_clipind_intre_arome_se_confirma(stare, monkeypatch):
    emise = _emise(monkeypatch)
    stare.process(IMG, _Res([_Box(1, 0.55)]), now=1000.0)   # chicken, cadrul 1
    assert emise == []                                       # inca neconfirmat
    stare.process(IMG, _Res([_Box(2, 0.52)]), now=1001.5)   # pigeon, cadrul 2
    assert len(emise) == 1                                   # grupul s-a confirmat
    assert emise[0] in ("gaina", "porumbel")


def test_schimbarea_aromei_nu_e_aparitie_noua(stare, monkeypatch):
    emise = _emise(monkeypatch)
    stare.process(IMG, _Res([_Box(1, 0.55)]), now=1000.0)
    stare.process(IMG, _Res([_Box(1, 0.55)]), now=1001.5)   # confirmat ca chicken
    stare.process(IMG, _Res([_Box(2, 0.60)]), now=1003.0)   # devine pigeon
    stare.process(IMG, _Res([_Box(2, 0.60)]), now=1004.5)
    assert len(emise) == 1                                   # tot O aparitie

def test_omul_are_nevoie_de_doua_cadre(stare, monkeypatch):
    emise = _emise(monkeypatch)
    stare.process(IMG, _Res([_Box(0, 0.9)]), now=1000.0)
    assert emise == []
    assert stare.last_boxes["boxes"][0]["filtru"] == "neconfirmat"   # overlay-ul explica
    stare.process(IMG, _Res([_Box(0, 0.9)]), now=1001.5)
    assert emise == ["om"]
    assert "filtru" not in stare.last_boxes["boxes"][0]


def test_vehiculul_se_confirma_dintr_un_cadru(stare, monkeypatch):
    emise = _emise(monkeypatch)
    stare.process(IMG, _Res([_Box(3, 0.9)]), now=1000.0)
    assert emise == ["masina"]


def test_amprenta_modelului(tmp_path, monkeypatch):
    """Fingerprinting: nume@sha12, cu cache invalidat de hot-swap (fine-tune)."""
    import hashlib
    mp = tmp_path / "model.pt"
    mp.write_bytes(b"greutati-v1")
    monkeypatch.setattr(detect, "MODEL_PATH", str(mp))
    monkeypatch.setattr(detect, "_amprenta", {"cheie": None, "val": None})
    a = detect.amprenta()
    assert a == "model.pt@" + hashlib.sha256(b"greutati-v1").hexdigest()[:12]
    assert detect.amprenta() == a                       # cache
    mp.write_bytes(b"greutati-v2-dupa-finetune")        # hot-swap pe disc
    b = detect.amprenta()
    assert b != a and b.startswith("model.pt@")


def test_evenimentul_de_detectie_poarta_amprenta(stare, monkeypatch, tmp_path):
    mp = tmp_path / "model.pt"
    mp.write_bytes(b"greutati")
    monkeypatch.setattr(detect, "MODEL_PATH", str(mp))
    monkeypatch.setattr(detect, "_amprenta", {"cheie": None, "val": None})
    prinse = []
    import hooks
    monkeypatch.setattr(hooks, "emit", lambda tip, cam=None, **d: prinse.append(d))
    stare.process(IMG, _Res([_Box(3, 0.9)]), now=1000.0)
    assert prinse and prinse[0]["model"] == detect.amprenta()
