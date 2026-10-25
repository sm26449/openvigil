"""Teste pentru app/store.py — scrierea JSON atomica (fundamentul persistentei sigure)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import store  # noqa: E402


def test_scrie_si_citeste(tmp_path):
    p = str(tmp_path / "a.json")
    store.atomic_write_json(p, {"x": 1, "ro": "ăî"})
    assert store.load_json(p) == {"x": 1, "ro": "ăî"}


def test_load_toleranta(tmp_path):
    assert store.load_json(str(tmp_path / "nu-exista.json"), {"d": 1}) == {"d": 1}
    bad = tmp_path / "stricat.json"
    bad.write_text("{ nu e json")
    assert store.load_json(str(bad), []) == []


def test_permisiuni_600(tmp_path):
    p = str(tmp_path / "secret.json")
    store.atomic_write_json(p, {"token": "x"}, mode=0o600)
    assert oct(os.stat(p).st_mode)[-3:] == "600"


def test_atomic_nu_lasa_tmp(tmp_path):
    p = str(tmp_path / "b.json")
    store.atomic_write_json(p, {"ok": True})
    # niciun fisier temporar ramas in director
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp")] == []


def test_atomic_pastreaza_vechiul_la_esec(tmp_path, monkeypatch):
    p = str(tmp_path / "c.json")
    store.atomic_write_json(p, {"v": 1})
    # simulam un esec la serializare (obiect ne-serializabil) -> fisierul vechi ramane intreg
    with pytest.raises(TypeError):
        store.atomic_write_json(p, {"v": object()})
    assert store.load_json(p) == {"v": 1}
    assert [f for f in os.listdir(tmp_path) if f.startswith(".tmp")] == []


def test_suprascrie_complet(tmp_path):
    p = str(tmp_path / "d.json")
    store.atomic_write_json(p, {"a": 1, "b": 2, "c": 3})
    store.atomic_write_json(p, {"a": 9})           # continut mai scurt
    assert store.load_json(p) == {"a": 9}          # fara ramasite din scrierea veche
    assert "b" not in json.loads(open(p).read())
