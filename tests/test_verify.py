"""Teste pentru app/verify.py — a doua opinie pe detectiile 'person' incerte.
Modelul stock nu se atinge: second_label se inlocuieste cu raspunsuri sintetice."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import verify  # noqa: E402


def _img():
    return np.zeros((480, 640, 3), np.uint8)


def _box(lbl="person", conf=0.6, x=0.3, y=0.3, w=0.2, h=0.4):
    return {"x": x, "y": y, "w": w, "h": h, "lbl": lbl, "conf": conf}


def test_corecteaza_person_dezmintit(monkeypatch):
    # stock vede 'car' pe crop -> person(0.6) devine car
    monkeypatch.setattr(verify, "second_label", lambda crop: ("car", 0.7))
    boxes = [_box("person", 0.6)]
    verify.refine(_img(), boxes)
    assert boxes[0]["lbl"] == "car" and boxes[0]["conf"] == 0.7 and boxes[0]["corectat"]


def test_nu_atinge_person_sigur(monkeypatch):
    # person foarte increzator (>= TRUST_ABOVE) nu se verifica deloc
    called = []
    monkeypatch.setattr(verify, "second_label", lambda crop: called.append(1) or ("car", 0.9))
    boxes = [_box("person", 0.95)]
    verify.refine(_img(), boxes)
    assert boxes[0]["lbl"] == "person" and not called


def test_nu_corecteaza_daca_stock_confirma_person(monkeypatch):
    monkeypatch.setattr(verify, "second_label", lambda crop: ("person", 0.8))
    boxes = [_box("person", 0.6)]
    verify.refine(_img(), boxes)
    assert boxes[0]["lbl"] == "person" and not boxes[0].get("corectat")


def test_nu_corecteaza_pe_stock_incert(monkeypatch):
    # stock vede 'dog' dar sub pragul STOCK_MIN -> nu e destul de sigur ca sa dezmintă
    monkeypatch.setattr(verify, "second_label", lambda crop: ("dog", 0.30))
    boxes = [_box("person", 0.6)]
    verify.refine(_img(), boxes)
    assert boxes[0]["lbl"] == "person"


def test_ignora_non_person(monkeypatch):
    monkeypatch.setattr(verify, "second_label",
                        lambda crop: (_ for _ in ()).throw(AssertionError("nu chema")))
    boxes = [_box("car", 0.6)]
    verify.refine(_img(), boxes)          # nu trebuie sa cheme second_label pt. non-person
    assert boxes[0]["lbl"] == "car"


def test_crop_geometrie():
    img = _img()
    c = verify._crop(img, (0.5, 0.5, 0.2, 0.2))
    assert c is not None and c.shape[0] > 0 and c.shape[1] > 0
    assert verify._crop(img, (2.0, 2.0, 0.1, 0.1)) is None   # in afara cadrului
