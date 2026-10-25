"""Teste pentru app/semantic.py — shard-uri, cautare, prune, parsare nume.

Modelul SigLIP nu se atinge: embed_text/embed_images se inlocuiesc cu vectori
sintetici, iar fisierele merg intr-un director temporar.
"""
import os
import sys

import numpy as np
import pytest
import torch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import semantic  # noqa: E402


@pytest.fixture(autouse=True)
def _semantic_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic, "SEM_DIR", str(tmp_path / "semantic"))
    monkeypatch.setattr(semantic, "SNAP_DIR", str(tmp_path / "snapshots"))


def _unit(v, dim=8):
    x = np.zeros(dim, np.float32)
    for i, val in enumerate(v):
        x[i] = val
    return x / np.linalg.norm(x)


def test_parse_fn():
    m = semantic._parse_fn("120530_cam101_person.jpg")
    assert m == {"fn": "120530_cam101_person.jpg", "time": "12:05:30",
                 "cam": 101, "label": "person"}
    assert semantic._parse_fn("altceva.jpg") is None


def test_shard_add_si_load():
    semantic.shard_add("2026-08-14", np.stack([_unit([1]), _unit([0, 1])]),
                       [{"fn": "a.jpg", "cam": 1, "label": "x", "time": "10:00:00"},
                        {"fn": "b.jpg", "cam": 2, "label": "y", "time": "10:01:00"}])
    mat, meta = semantic.shard_load("2026-08-14")
    assert mat.shape == (2, 8) and len(meta) == 2
    # append la shard existent
    semantic.shard_add("2026-08-14", np.stack([_unit([0, 0, 1])]),
                       [{"fn": "c.jpg", "cam": 1, "label": "z", "time": "10:02:00"}])
    mat, meta = semantic.shard_load("2026-08-14")
    assert mat.shape == (3, 8) and meta[2]["fn"] == "c.jpg"


def test_shard_load_inexistent():
    mat, meta = semantic.shard_load("1999-01-01")
    assert mat is None and meta == []


def test_search_ordoneaza_dupa_similaritate(monkeypatch):
    semantic.shard_add("2026-08-14",
                       np.stack([_unit([1]), _unit([0, 1]), _unit([1, 0.2])]),
                       [{"fn": "a.jpg", "cam": 1, "label": "car", "time": "10:00:00"},
                        {"fn": "b.jpg", "cam": 2, "label": "person", "time": "10:01:00"},
                        {"fn": "c.jpg", "cam": 3, "label": "car", "time": "10:02:00"}])
    monkeypatch.setattr(semantic, "embed_text", lambda q: _unit([1]))
    rez = semantic.search("masina", days=7, limit=10)
    assert [r["fn"] if "fn" in r else r["url"].split("/")[-1] for r in rez][:2] == \
           ["a.jpg", "c.jpg"]
    assert rez[0]["score"] >= rez[1]["score"] >= rez[2]["score"]
    assert rez[0]["url"] == "/snapshots/2026-08-14/a.jpg"


def test_search_filtreaza_camerele_viewerului(monkeypatch):
    semantic.shard_add("2026-08-14", np.stack([_unit([1]), _unit([1])]),
                       [{"fn": "a.jpg", "cam": 1, "label": "car", "time": "10:00:00"},
                        {"fn": "b.jpg", "cam": 2, "label": "car", "time": "10:01:00"}])
    monkeypatch.setattr(semantic, "embed_text", lambda q: _unit([1]))
    rez = semantic.search("x", visible={2})
    assert len(rez) == 1 and rez[0]["cam"] == 2


def test_search_pe_mai_multe_zile(monkeypatch):
    semantic.shard_add("2026-08-13", np.stack([_unit([1])]),
                       [{"fn": "vechi.jpg", "cam": 1, "label": "car", "time": "09:00:00"}])
    semantic.shard_add("2026-08-14", np.stack([_unit([0, 1])]),
                       [{"fn": "nou.jpg", "cam": 1, "label": "car", "time": "10:00:00"}])
    monkeypatch.setattr(semantic, "embed_text", lambda q: _unit([1]))
    rez = semantic.search("x", days=7)
    assert rez[0]["url"].endswith("vechi.jpg")   # cel mai similar castiga, indiferent de zi
    # limitarea la 1 zi vede doar ziua cea mai noua
    rez1 = semantic.search("x", days=1)
    assert len(rez1) == 1 and rez1[0]["url"].endswith("nou.jpg")


def test_prune_sterge_shardurile_orfane(monkeypatch):
    # testam logica GAZDEI (nvr-ai); altfel, cu ai_service pornit in instalarea
    # reala, prune ar incerca sa se trimita singur prin HTTP (#9 pas 3)
    monkeypatch.setattr(semantic, "IS_HOST", True)
    semantic.shard_add("2026-08-10", np.stack([_unit([1])]),
                       [{"fn": "a.jpg", "cam": 1, "label": "x", "time": "10:00:00"}])
    os.makedirs(os.path.join(semantic.SNAP_DIR, "2026-08-11"))
    semantic.shard_add("2026-08-11", np.stack([_unit([1])]),
                       [{"fn": "b.jpg", "cam": 1, "label": "x", "time": "10:00:00"}])
    semantic.prune()
    assert semantic.indexed_days() == ["2026-08-11"]   # 08-10 nu mai are poze -> a zburat


def test_status_numara_corect():
    assert semantic.status()["indexed"] == 0
    semantic.shard_add("2026-08-14", np.stack([_unit([1]), _unit([0, 1])]),
                       [{"fn": "a.jpg", "cam": 1, "label": "x", "time": "10:00:00"},
                        {"fn": "b.jpg", "cam": 1, "label": "x", "time": "10:01:00"}])
    s = semantic.status()
    assert s["indexed"] == 2 and s["days"] == 1


def test_match_dtype_casteaza_doar_floaturile():
    # plasa de dtype: intrarile float se leaga de dtype-ul modelului (float32), intregii nu
    class _M:
        dtype = torch.float32

    ins = {"pixel_values": torch.zeros(1, 3, dtype=torch.bfloat16),
           "input_ids": torch.zeros(1, 3, dtype=torch.long)}
    out = semantic._match_dtype(ins, _M())
    assert out["pixel_values"].dtype == torch.float32
    assert out["input_ids"].dtype == torch.long
