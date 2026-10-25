"""faces: id-uri stabile intre regrupari, ultima aparitie din numele crop-urilor, unirea persoanelor."""
import json
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import faces  # noqa: E402


def _v(seed):
    v = np.random.default_rng(seed).normal(size=512)
    return v / np.linalg.norm(v)


def test_potriveste_ids_stabile():
    a, b = _v(1), _v(2)
    vechi = {"nec-1": a.tolist(), "nec-2": b.tolist()}
    # aceleasi grupuri, in alta ordine -> aceleasi id-uri; un grup nou -> id nou
    ids, contor = faces.potriveste_ids([b, a, _v(3)], vechi, 2)
    assert ids == ["nec-2", "nec-1", "nec-3"] and contor == 3
    # un id vechi nu se da de doua ori, chiar daca doi centroizi ii seamana
    ids, contor = faces.potriveste_ids([a, a * 0.98 + _v(9) * 0.02], {"nec-1": a.tolist()}, 1)
    assert ids[0] == "nec-1" and ids[1] == "nec-2"


def test_ultima_aparitie():
    u = faces._ultima_aparitie(["unknown/2026-10-20/101010_cam3.jpg", "unknown/2026-10-24/093000_cam101.jpg",
                                "unknown/2026-10-24/083000_cam5.jpg", "altceva.jpg"])
    assert u == {"zi": "2026-10-24", "ora": "09:30:00", "cam": 101}
    assert faces._ultima_aparitie([]) is None


def test_uneste_muta_pozele(tmp_path, monkeypatch):
    persons = tmp_path / "persons"
    (persons / "ana").mkdir(parents=True); (persons / "ana2").mkdir()
    (persons / "ana" / "p1.jpg").write_bytes(b"a"); (persons / "ana2" / "p1.jpg").write_bytes(b"b"); (persons / "ana2" / "p2.jpg").write_bytes(b"c")
    monkeypatch.setattr(faces, "PERSONS_DIR", str(persons))
    emb = {"ana": np.zeros((1, 512)), "ana2": np.zeros((2, 512))}
    monkeypatch.setattr(faces, "_load_embeddings", lambda: emb)
    salvat = {}
    monkeypatch.setattr(faces, "_save_embeddings", lambda d: salvat.update(d))
    monkeypatch.setattr(faces, "_rebuild_person", lambda n: 3)
    monkeypatch.setattr(faces, "regrupeaza", lambda: None)
    r = faces.uneste("ana", "ana2")
    assert r == {"nume": "ana", "poze_mutate": 2, "embeddings": 3}
    assert sorted(os.listdir(persons / "ana")) == ["p1-ana2.jpg", "p1.jpg", "p2.jpg"]   # coliziunea e redenumita
    assert not (persons / "ana2").exists() and "ana2" not in salvat
    with pytest.raises(ValueError):
        faces.uneste("ana", "ana")
    with pytest.raises(ValueError):
        faces.uneste("ana", "nu-exista")
