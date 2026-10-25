"""Teste pentru app/describe.py — logica din jurul VLM-ului (modelul nu se atinge)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import describe  # noqa: E402
import rules  # noqa: E402
import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    describe._last.clear()
    monkeypatch.setattr(settings, "get",
                        lambda k: True if k == "person_describe" else None)


def test_process_respecta_cooldown(monkeypatch):
    texte = iter(["barbat cu rucsac.", "alt text."])
    monkeypatch.setattr(describe, "describe_crop", lambda img: next(texte))
    emise = []
    import hooks
    monkeypatch.setattr(hooks, "emit", lambda *a, **k: emise.append((a, k)))
    img = np.zeros((360, 640, 3), np.uint8)
    box = [100, 50, 220, 340]   # 120x290 px, peste MIN_SIDE
    assert describe.process(7, img, [box]) == "barbat cu rucsac."
    assert describe.process(7, img, [box]) is None      # sub cooldown
    assert len(emise) == 1 and emise[0][1]["text"].startswith("barbat")


def test_process_sare_crop_urile_mici(monkeypatch):
    monkeypatch.setattr(describe, "describe_crop",
                        lambda img: pytest.fail("nu trebuia chemat"))
    img = np.zeros((360, 640, 3), np.uint8)
    assert describe.process(7, img, [[10, 10, 40, 55]]) is None   # 30x45 < MIN_SIDE


def test_process_oprit_nu_face_nimic(monkeypatch):
    monkeypatch.setattr(settings, "get", lambda k: False)
    assert describe.process(7, None, [[0, 0, 100, 100]]) is None


def test_regula_pe_continutul_descrierii():
    assert rules._value_match("descriere", "rucsac",
                              {"text": "Barbat de ~40 de ani cu RUCSAC negru."})
    assert not rules._value_match("descriere", "umbrela",
                                  {"text": "Barbat cu rucsac."})
    assert rules._value_match("descriere", "", {"text": "orice"})   # gol = orice
