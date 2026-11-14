"""Triajul pe grupuri (audit UX 2026-11-14, UX-F): miniatura incidentului = cadrul salvat cel mai
apropiat, iar starea se poate pune pe mai multe incidente dintr-o singura scriere."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import incidents  # noqa: E402
import snaps  # noqa: E402


def test_cadrul_cel_mai_apropiat(tmp_path, monkeypatch):
    zi = tmp_path / "2026-11-14"
    zi.mkdir()
    for fn in ["120000_cam7_person.jpg", "120130_cam7_person.jpg", "120100_cam8_car.jpg", "gunoi.txt"]:
        (zi / fn).write_bytes(b"x")
    monkeypatch.setattr(snaps, "SNAP_DIR", str(tmp_path))
    snaps._index.clear()
    mij = 12 * 3600 + 80
    assert snaps.cel_mai_apropiat(7, "2026-11-14", mij - 30, mij + 30) == "120130_cam7_person.jpg"
    assert snaps.cel_mai_apropiat(7, "2026-11-14", 0, 100) is None          # nimic in interval
    assert snaps.cel_mai_apropiat(9, "2026-11-14", 0, 86400) is None        # alta camera
    assert snaps.cel_mai_apropiat(7, "../etc", 0, 86400) is None            # zi invalida


def test_poza_episodului_din_intervalul_lui(tmp_path, monkeypatch):
    zi = tmp_path / "2026-11-14"
    zi.mkdir()
    (zi / "234430_cam6_person.jpg").write_bytes(b"x")
    monkeypatch.setattr(snaps, "SNAP_DIR", str(tmp_path))
    snaps._index.clear()
    ev = {"cam": 6, "t": "2026-11-14 23:49:00", "tip": "episod", "inceput": "23:44:06", "durata_sec": 45}
    incidents.adauga_poze([ev])
    assert ev["poza"] == "/api/flux/thumb/2026-11-14/234430_cam6_person.jpg"


def test_stare_pe_bloc(tmp_path, monkeypatch):
    monkeypatch.setattr(incidents, "ACK_FILE", str(tmp_path / "ack.json"))
    assert incidents.set_state_bloc(["a", "b", "c"], "fals", who="op") == 3
    d = incidents._load()
    assert {k: v["stare"] for k, v in d.items()} == {"a": "fals", "b": "fals", "c": "fals"}
    incidents.set_state_bloc(["a"], "nou")
    assert "a" not in incidents._load()
