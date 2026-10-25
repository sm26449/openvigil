"""Marcaje pe inregistrari (app/marcaje.py): CRUD, validare, filtrul pe zi, camere permise."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import jurnal  # noqa: E402
import marcaje  # noqa: E402


def test_create_moment_si_get():
    m = marcaje.create(14, "2026-10-23 10:20:30", eticheta="masina gri", who="admin")
    assert m["cam"] == 14 and m["t"] == m["t_end"] == "2026-10-23 10:20:30"
    assert m["durata"] == 0 and m["eticheta"] == "masina gri" and m["creat_de"] == "admin"
    assert marcaje.get(m["id"]) == m
    assert marcaje.get("nuexista") is None


def test_create_interval_si_validari():
    m = marcaje.create(3, "2026-10-23T10:00:00", end="2026-10-23 10:05:00", nota="x" * 600)
    assert m["durata"] == 300 and len(m["nota"]) == 500
    with pytest.raises(ValueError):
        marcaje.create(3, "2026-10-23 10:05:00", end="2026-10-23 10:00:00")
    with pytest.raises(ValueError):
        marcaje.create(3, "2026-10-23 10:00:00", end="2026-10-25 10:00:01")
    with pytest.raises(ValueError):
        marcaje.create(3, "ieri pe la pranz")
    with pytest.raises(ValueError):
        marcaje.create("abc", "2026-10-23 10:00:00")


def test_lista_filtre_cam_zi_si_ordine():
    marcaje.create(1, "2026-10-22 23:50:00", end="2026-10-23 00:10:00", eticheta="peste noapte")
    marcaje.create(1, "2026-10-23 08:00:00", eticheta="dimineata")
    marcaje.create(2, "2026-10-23 09:00:00", eticheta="alta camera")
    marcaje.create(1, "2026-10-24 09:00:00", eticheta="maine")
    azi = marcaje.lista(cam=1, day="2026-10-23")
    assert [m["eticheta"] for m in azi] == ["dimineata", "peste noapte"]   # cele mai noi primele
    assert [m["eticheta"] for m in marcaje.lista(day="2026-10-23")] == \
        ["alta camera", "dimineata", "peste noapte"]
    assert [m["eticheta"] for m in marcaje.lista(cam=1, day="2026-10-22")] == ["peste noapte"]
    assert marcaje.lista(day="zi-invalida") == []
    assert len(marcaje.lista()) == 4


def test_lista_camere_permise():
    marcaje.create(1, "2026-10-23 08:00:00")
    marcaje.create(2, "2026-10-23 09:00:00")
    assert [m["cam"] for m in marcaje.lista(visible={2})] == [2]
    assert marcaje.lista(visible=set()) == []
    assert len(marcaje.lista(visible=None)) == 2


def test_update_si_delete():
    m = marcaje.create(5, "2026-10-23 10:00:00", eticheta="a")
    m2 = marcaje.update(m["id"], eticheta="b", nota="nota", end="2026-10-23 10:02:00")
    assert m2["eticheta"] == "b" and m2["nota"] == "nota" and m2["durata"] == 120
    with pytest.raises(ValueError):
        marcaje.update(m["id"], end="2026-10-23 09:00:00")
    assert marcaje.update("nuexista", eticheta="x") is None
    assert marcaje.delete(m["id"]) is True
    assert marcaje.delete(m["id"]) is False
    assert marcaje.lista() == []


def test_sta_in_aceeasi_baza_cu_jurnalul():
    marcaje.create(5, "2026-10-23 10:00:00")
    jurnal.insert({"t": "2026-10-23 10:00:00", "tip": "detectie", "cam": 5})
    c = jurnal.conexiune()
    tabele = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"events", "marcaje"} <= tabele
    jurnal.reset()
    assert len(marcaje.lista()) == 1 and len(jurnal.query()) == 1   # persistate
