"""record.goluri: golurile de inregistrare dintr-un interval (lipsa la inceput, intre
segmente, la sfarsit; granitele dintre fisiere nu sunt goluri)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import record  # noqa: E402


def test_goluri(monkeypatch):
    t0 = 1_800_000_000
    seg = [(t0, "a", "a"), (t0 + 300, "b", "b"), (t0 + 900, "c", "c")]   # lipseste 600..900
    monkeypatch.setattr(record, "_files", lambda cam, arhiva=True: seg)
    assert record.goluri(5, t0, t0 + 600) == []                              # continuu
    assert record.goluri(5, t0 + 100, t0 + 1000) == [(t0 + 600, t0 + 900)]  # gol la mijloc
    assert record.goluri(5, t0 - 100, t0 + 100) == [(t0 - 100, t0)]         # lipsa la inceput
    assert record.goluri(5, t0 + 1100, t0 + 1300) == [(t0 + 1200, t0 + 1300)]  # lipsa la sfarsit
    assert record.goluri(5, t0 + 590, t0 + 610) == []                       # 10 s sub prag: granita, nu gol
    assert record.goluri(5, t0 + 590, t0 + 630) == [(t0 + 600, t0 + 630)]   # 30 s: gol
    monkeypatch.setattr(record, "_files", lambda cam, arhiva=True: [])
    assert record.goluri(5, t0, t0 + 60) == [(t0, t0 + 60)]
