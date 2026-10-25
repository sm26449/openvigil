"""record.fn_epoch fara strptime: acelasi rezultat ca strptime pentru nume UTC si locale;
_listeaza cu cache pe mtime-ul directorului."""
import calendar
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import record  # noqa: E402


def test_fn_epoch_egal_cu_strptime():
    for fn, utc in (("2026-10-23_18-35-10Z.mp4", True), ("2026-08-15_09-11-26.mp4", False),
                    ("2025-01-01_00-00-00Z.mp4", True)):
        st = time.strptime(fn[:19], "%Y-%m-%d_%H-%M-%S")
        asteptat = calendar.timegm(st) if utc else time.mktime(st)
        assert record.fn_epoch(fn) == asteptat
    assert record.fn_epoch("altceva.mp4") is None


def test_listeaza_cache_pe_mtime(tmp_path):
    d = tmp_path / "cam7"
    d.mkdir()
    (d / "2026-10-23_18-35-10Z.mp4").write_bytes(b"x")
    a = record._listeaza(str(tmp_path), 7)
    assert [x[2] for x in a] == ["2026-10-23_18-35-10Z.mp4"]
    (d / "2026-10-23_18-40-10Z.mp4").write_bytes(b"x")
    os.utime(d, (time.time() + 5, time.time() + 5))     # mtime nou -> lista refacuta
    b = record._listeaza(str(tmp_path), 7)
    assert len(b) == 2
    b.append("gunoi")                                   # apelantul primeste o copie
    assert len(record._listeaza(str(tmp_path), 7)) == 2
