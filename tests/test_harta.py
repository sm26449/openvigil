"""Harta (app/harta.py): planuri, pozitii normalizate, o camera pe un singur plan, imaginea."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import harta  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(harta, "FILE", str(tmp_path / "harta.json"))
    monkeypatch.setattr(harta, "DIR", str(tmp_path / "harta"))


def test_planuri_si_pozitii():
    assert harta.lista() == []
    p = harta.plan_nou("  Curtea  ")
    assert p["nume"] == "Curtea" and len(p["id"]) == 8
    harta.aseaza(p["id"], {"5": {"x": 0.25, "y": 0.5, "dir": 450, "fov": 500},
                           7: {"x": 1, "y": 0}})
    cams = harta.lista()[0]["camere"]
    assert cams["5"] == {"x": 0.25, "y": 0.5, "dir": 90.0, "fov": 180.0}
    assert cams["7"] == {"x": 1.0, "y": 0.0, "dir": 0.0, "fov": 70.0}
    with pytest.raises(ValueError):
        harta.aseaza(p["id"], {"5": {"x": 1.2, "y": 0}})
    with pytest.raises(ValueError):
        harta.aseaza("nuexista", {})
    harta.aseaza(p["id"], {"5": None})
    assert "5" not in harta.lista()[0]["camere"]
    # restrictia pe camere: contul vede doar ale lui
    assert harta.lista(visible={5})[0]["camere"] == {}
    assert "7" in harta.lista(visible={7})[0]["camere"]
    harta.redenumeste(p["id"], "Casa")
    assert harta.lista()[0]["nume"] == "Casa"


def test_o_camera_pe_un_singur_plan():
    a = harta.plan_nou("A")
    b = harta.plan_nou("B")
    harta.aseaza(a["id"], {"5": {"x": 0.1, "y": 0.1}})
    harta.aseaza(b["id"], {"5": {"x": 0.9, "y": 0.9}})
    pl = {p["id"]: p for p in harta.lista()}
    assert "5" not in pl[a["id"]]["camere"] and "5" in pl[b["id"]]["camere"]


def test_imagine_validare_si_stergere(tmp_path):
    p = harta.plan_nou("A")
    with pytest.raises(ValueError):
        harta.salveaza_imagine(p["id"], PNG, "text/plain")
    with pytest.raises(ValueError):
        harta.salveaza_imagine(p["id"], b"nu e imagine", "image/png")
    with pytest.raises(ValueError):
        harta.salveaza_imagine("nuexista", PNG, "image/png")
    cale = harta.salveaza_imagine(p["id"], PNG, "image/png")
    assert cale.endswith(".png") and harta.lista()[0]["imagine"] is True
    assert oct(os.stat(cale).st_mode)[-3:] == "600"
    # jpeg peste png: ramane un singur fisier
    harta.salveaza_imagine(p["id"], b"\xff\xd8\xff" + b"\x00" * 10, "image/jpeg")
    assert harta.imagine(p["id"]).endswith(".jpg") and not os.path.exists(cale)
    harta.sterge(p["id"])
    assert harta.lista() == [] and harta.imagine(p["id"]) is None
    with pytest.raises(ValueError):
        harta.sterge(p["id"])
