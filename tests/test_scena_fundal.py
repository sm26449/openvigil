"""Fundalul invatat din verdicte (app/scena_fundal.py): exemple per camera, potrivirea cu prag,
scoaterea cand verdictul se schimba, reconstruirea din feedback.jsonl. SigLIP e inlocuit cu
vectori deterministi din numele pozei."""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import scena_fundal as F  # noqa: E402
import scena_web  # noqa: E402


def _vec(seed):
    rng = np.random.default_rng(seed)
    v = rng.normal(size=768).astype(np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(F, "SCENA_DIR", str(tmp_path))
    monkeypatch.setattr(scena_web, "SCENA_DIR", str(tmp_path))
    monkeypatch.setattr(scena_web, "FEEDBACK", str(tmp_path / "feedback.jsonl"))
    (tmp_path / "14").mkdir()
    for fn in ("a.jpg", "b.jpg", "c.jpg"):
        (tmp_path / "14" / fn).write_bytes(b"x")
    monkeypatch.setattr(F, "_vector", lambda cam, poza: _vec(hash(poza) % 1000))
    F._cache.clear()


def test_adauga_potriveste_scoate():
    assert F.adauga(14, "a.jpg", "frunze mișcate de vânt")
    assert not F.adauga(14, "a.jpg", "frunze mișcate de vânt")        # deja
    assert F.adauga(14, "a.jpg", "copac") and F.numar(14) == 1       # alt nume, acelasi exemplu
    assert not F.adauga(14, "b.jpg", "pisică")                        # nu e nume de fundal
    assert F.adauga(14, "b.jpg", "nimic") and F.numar(14) == 2
    va = _vec(hash("a.jpg") % 1000)
    aproape = va + 0.3 * _vec(5)
    aproape /= np.linalg.norm(aproape)
    r = F.potriveste(14, aproape)
    assert r and r["nume"] == "copac" and r["poza"] == "a.jpg" and r["scor"] >= F.PRAG
    assert F.potriveste(14, _vec(77)) is None                         # vector strain: sub prag
    assert F.potriveste(6, va) is None and F.potriveste(14, None) is None
    assert F.scoate(14, "a.jpg") and not F.scoate(14, "a.jpg") and F.numar(14) == 1
    assert F.potriveste(14, va) is None


def test_reconstruieste_din_feedback():
    scena_web.feedback_adauga(14, "i1", "gresit", eticheta="frunze mișcate de vânt", poza="a.jpg")
    scena_web.feedback_adauga(14, "i2", "gresit", eticheta="nimic", poza="b.jpg")
    scena_web.feedback_adauga(14, "i3", "ok", poza="c.jpg")
    assert F.reconstruieste() == (2, 0) and F.numar(14) == 2
    scena_web.feedback_adauga(14, "i2", "gresit", eticheta="pisică", poza="b.jpg")   # s-a razgandit
    assert F.reconstruieste() == (0, 1) and [m["poza"] for m in F.incarca(14)[1]] == ["a.jpg"]
    assert F.reconstruieste() == (0, 0)
