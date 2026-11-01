"""Exemplele invatate din verdicte (app/scena_fundal.py): fundal si obiecte per camera, cele
doua praguri, ce eticheta au voie sa bata, scoaterea cand verdictul se schimba, reconstruirea
din feedback.jsonl. SigLIP e inlocuit cu vectori deterministi din numele pozei."""
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


def _aproape(poza, cat=0.3):
    v = _vec(hash(poza) % 1000) + cat * _vec(5)
    return v / np.linalg.norm(v)


def test_adauga_potriveste_scoate():
    assert F.adauga(14, "a.jpg", "frunze mișcate de vânt")
    assert not F.adauga(14, "a.jpg", "frunze mișcate de vânt")        # deja
    assert F.adauga(14, "a.jpg", "copac") and F.numar(14) == (1, 0)  # alt nume, acelasi exemplu
    assert not F.adauga(14, "b.jpg", "necunoscut") and not F.adauga(14, "b.jpg", "")
    assert F.adauga(14, "b.jpg", "pisică") and F.numar(14) == (1, 1)  # obiect
    r = F.potriveste(14, _aproape("a.jpg"))
    assert r and r["nume"] == "copac" and r["poza"] == "a.jpg" and r["scor"] >= F.PRAG_FUNDAL and r["fundal"]
    r = F.potriveste(14, _aproape("b.jpg"))
    assert r and r["nume"] == "pisică" and not r["fundal"]
    assert F.potriveste(14, _vec(77)) is None                         # vector strain: sub prag
    assert F.potriveste(6, _vec(1)) is None and F.potriveste(14, None) is None
    assert F.scoate(14, "a.jpg") and not F.scoate(14, "a.jpg") and F.numar(14) == (0, 1)
    assert F.potriveste(14, _aproape("a.jpg")) is None


def test_praguri_diferite(monkeypatch):
    """Un obiect trece la 0,82-0,85, un fundal nu; castiga cel mai apropiat dintre cei care trec."""
    emb, o1, o2 = _vec(1), _vec(2), _vec(3)
    o1 -= o1 @ emb * emb; o1 /= np.linalg.norm(o1)          # ortogonale pe emb: cosinusul e exact
    o2 -= o2 @ emb * emb; o2 /= np.linalg.norm(o2)
    meta = [{"poza": "a.jpg", "nume": "nimic"}, {"poza": "b.jpg", "nume": "găină"}]

    def cu(ca, cb):
        mat = np.stack([ca * emb + np.sqrt(1 - ca * ca) * o1, cb * emb + np.sqrt(1 - cb * cb) * o2]).astype(np.float32)
        monkeypatch.setattr(F, "incarca", lambda cam: (mat, meta))
    cu(0.83, 0.83)                                            # fundalul (0,85) nu trece, gaina (0,82) da
    r = F.potriveste(14, emb)
    assert r and r["nume"] == "găină" and abs(r["scor"] - 0.83) < 0.002
    cu(0.86, 0.84)                                            # amandoua trec: cel mai apropiat
    assert F.potriveste(14, emb)["nume"] == "nimic"
    cu(0.84, 0.81)
    assert F.potriveste(14, emb) is None


def test_aplica_cine_bate_pe_cine(monkeypatch):
    F.adauga(14, "a.jpg", "nimic")
    F.adauga(14, "b.jpg", "găină")
    rez = {"eticheta": "necunoscut", "incredere": 0, "sursa": None}
    assert F.aplica(14, _aproape("b.jpg"), rez) and rez["eticheta"] == "găină" and rez["sursa"] == F.SURSA_OBIECT
    rez = {"eticheta": "porumbel", "incredere": 0.09, "sursa": "vocabular"}
    assert F.aplica(14, _aproape("b.jpg"), rez) and rez["eticheta"] == "găină"      # vocabularul nesigur cade
    rez = {"eticheta": "person", "incredere": 0.8, "sursa": "yolo"}
    assert F.aplica(14, _aproape("b.jpg"), rez) is None and rez["eticheta"] == "person"   # detectorul ramane
    assert F.aplica(14, _aproape("a.jpg"), rez) and rez["eticheta"] == "nimic" and rez["sursa"] == F.SURSA  # fundalul bate tot
    assert F.aplica(14, _vec(77), rez) is None


def test_nume_exemplu():
    assert F.nume_exemplu({"verdict": "gresit", "eticheta": "nimic"}) == "nimic"
    assert F.nume_exemplu({"verdict": "gresit", "eticheta": "cal cu căruță"}) == "cal cu căruță"
    assert F.nume_exemplu({"verdict": "ok", "eticheta": None, "eticheta_initiala": "găină"}) == "găină"
    assert F.nume_exemplu({"verdict": "ok", "eticheta": None, "eticheta_initiala": "necunoscut"}) is None
    assert F.nume_exemplu({"verdict": "ok"}) is None
    assert F.nume_exemplu({"verdict": "necunoscut", "eticheta": "găină"}) is None


def test_reconstruieste_din_feedback():
    scena_web.feedback_adauga(14, "i1", "gresit", eticheta="frunze mișcate de vânt", poza="a.jpg")
    scena_web.feedback_adauga(14, "i2", "gresit", eticheta="nimic", poza="b.jpg")
    scena_web.feedback_adauga(14, "i3", "ok", poza="c.jpg")                           # fara nume propus: nu e exemplu
    assert F.reconstruieste() == (2, 0) and F.numar(14) == (2, 0)
    scena_web.feedback_adauga(14, "i2", "gresit", eticheta="pisică", poza="b.jpg")   # s-a razgandit: obiect
    assert F.reconstruieste() == (1, 0) and F.numar(14) == (1, 1)
    scena_web.feedback_adauga(14, "i2", "necunoscut", poza="b.jpg")                  # nu se vede: iese
    assert F.reconstruieste() == (0, 1) and [m["poza"] for m in F.incarca(14)[1]] == ["a.jpg"]
    scena_web.feedback_adauga(14, "i3", "ok", eticheta="găină", poza="c.jpg")
    assert F.reconstruieste() == (1, 0) and F.numar(14) == (1, 1)
    assert F.reconstruieste() == (0, 0)
