"""Probe („pastreaza ca proba”, app/probe.py): CRUD, validare, filtre, termen si
intervalele active folosite de retentie (acopera/active_ranges)."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import probe  # noqa: E402
import db  # noqa: E402


@pytest.fixture(autouse=True)
def _pg():
    try:
        db.ping()
    except Exception as e:
        pytest.skip(f"PostgreSQL indisponibil: {e}")
    probe._init()
    with db.conn() as c:
        c.execute("TRUNCATE probe")
    yield


def test_create_interval_si_get():
    p = probe.create(14, "2026-10-23 10:00:00", "2026-10-23 10:05:00",
                     motiv="efractie", who="admin")
    assert p["cam"] == 14 and p["durata"] == 300 and p["motiv"] == "efractie"
    assert p["creat_de"] == "admin" and p["sursa"] == "manual"
    assert p["expira"] is False and p["pana_la"] is None
    assert probe.get(p["id"]) == p
    assert probe.get("nuexista") is None


def test_validari():
    with pytest.raises(ValueError):
        probe.create(3, "2026-10-23 10:05:00", "2026-10-23 10:00:00")   # sfarsit < inceput
    with pytest.raises(ValueError):
        probe.create(3, "2026-10-23 10:00:00", "2026-10-30 10:00:01")   # > 7 zile
    with pytest.raises(ValueError):
        probe.create(3, "candva", "altcandva")
    with pytest.raises(ValueError):
        probe.create("abc", "2026-10-23 10:00:00", "2026-10-23 10:01:00")


def test_termen_si_update():
    pl = int(time.time()) + 3600
    p = probe.create(5, "2026-10-23 10:00:00", "2026-10-23 10:05:00", pana_la=pl)
    assert p["expira"] is True and p["pana_la"] == pl
    p2 = probe.update(p["id"], motiv="nou", pana_la=None)   # pe termen nedefinit
    assert p2["motiv"] == "nou" and p2["pana_la"] is None and p2["expira"] is False
    # 'keep' nu atinge termenul
    p3 = probe.update(p["id"], motiv="iar")
    assert p3["pana_la"] is None
    assert probe.update("nuexista", motiv="x") is None


def test_lista_filtre_si_camere_permise():
    probe.create(1, "2026-10-22 23:50:00", "2026-10-23 00:10:00", motiv="noapte")
    probe.create(1, "2026-10-23 08:00:00", "2026-10-23 08:10:00", motiv="dim")
    probe.create(2, "2026-10-23 09:00:00", "2026-10-23 09:10:00", motiv="alta")
    assert [p["motiv"] for p in probe.lista(cam=1, day="2026-10-23")] == ["dim", "noapte"]
    assert [p["motiv"] for p in probe.lista(cam=1, day="2026-10-22")] == ["noapte"]
    assert [p["cam"] for p in probe.lista(visible={2})] == [2]
    assert probe.lista(visible=set()) == []
    assert len(probe.lista()) == 3


def test_delete():
    p = probe.create(5, "2026-10-23 10:00:00", "2026-10-23 10:05:00")
    assert probe.delete(p["id"]) is True
    assert probe.delete(p["id"]) is False
    assert probe.lista() == []


def test_acopera():
    r = [(100, 200), (500, 600)]
    assert probe.acopera(r, 150, 160) is True       # inauntru
    assert probe.acopera(r, 50, 120) is True        # atinge marginea
    assert probe.acopera(r, 200, 500) is True        # atinge capetele ambelor
    assert probe.acopera(r, 250, 400) is False      # intre ele
    assert probe.acopera(None, 1, 2) is False
    assert probe.acopera([], 1, 2) is False


def test_active_ranges_sare_cele_expirate():
    now = int(time.time())
    # doua pe aceeasi camera; una expirata, una pe termen nedefinit
    probe.create(7, "2026-10-23 10:00:00", "2026-10-23 10:05:00")          # nedefinit
    probe.create(7, "2026-10-23 11:00:00", "2026-10-23 11:05:00", pana_la=now - 10)  # expirata
    probe.create(8, "2026-10-23 12:00:00", "2026-10-23 12:05:00", pana_la=now + 3600)  # in viitor
    act = probe.active_ranges(now)
    assert len(act[7]) == 1          # cea expirata nu mai protejeaza
    assert len(act[8]) == 1
