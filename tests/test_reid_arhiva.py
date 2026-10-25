"""reid_arhiva: scriere pe zile, cautare cu grupare pe camera/minut, embedding din arhiva, retentie."""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import reid_arhiva  # noqa: E402


def _v(seed):
    v = np.random.default_rng(seed).normal(size=512).astype(np.float32)
    return v / np.linalg.norm(v)


def test_adauga_si_cauta(tmp_path, monkeypatch):
    monkeypatch.setattr(reid_arhiva, "DIR", str(tmp_path))
    reid_arhiva._cache.clear()
    crop = np.zeros((200, 80, 3), np.uint8)
    a, b = _v(1), _v(2)
    t0 = time.mktime(time.strptime("2026-10-24 10:00:00", "%Y-%m-%d %H:%M:%S"))
    reid_arhiva.adauga(3, 11, a, crop, cine="stefan", t=t0)
    reid_arhiva.adauga(3, 11, a * 0.95 + b * 0.05, crop, t=t0 + 20)        # acelasi minut, aceeasi camera
    reid_arhiva.adauga(5, 12, a * 0.9 + b * 0.1, crop, t=t0 + 600)          # alta camera, 10 min mai tarziu
    reid_arhiva.adauga(5, 13, b, crop, t=t0 + 700)                           # alt om
    reid_arhiva.adauga(7, 14, a, crop, t=t0 - 86400)                         # ieri
    assert reid_arhiva.zile() == ["2026-10-24", "2026-10-23"]
    h = reid_arhiva.cauta(a, zile_max=7)
    cams = [(x["zi"], x["cam"]) for x in h]
    assert ("2026-10-24", 3) in cams and ("2026-10-24", 5) in cams and ("2026-10-23", 7) in cams
    assert sum(1 for x in h if x["cam"] == 3) == 1                            # grupat pe minut
    assert all(x["sim"] >= reid_arhiva.PRAG for x in h) and h[0]["sim"] >= h[-1]["sim"]
    assert not any(x["tid"] == 13 for x in h)                                 # alt om, sub prag
    assert h[0].get("cine") == "stefan" or any(x.get("cine") == "stefan" for x in h)
    # crop-ul mic e scris si se gaseste; embedding-ul se poate lua inapoi din arhiva
    fn = [x for x in h if x["cam"] == 3][0]["fn"]
    assert reid_arhiva.cale_crop("2026-10-24", fn) and not reid_arhiva.cale_crop("../x", fn)
    e = reid_arhiva.embedding_din("2026-10-24", fn)
    assert e is not None and float(e @ a) > 0.99
    # doar camerele vizibile
    assert all(x["cam"] == 5 for x in reid_arhiva.cauta(a, vizibile={5}))
    # retentie: ieri dispare la 1 zi pastrata... (pragul e pe zile calendaristice de azi, deci simulam)
    assert reid_arhiva.stare()["zile"] == 2


def test_curata_sterge_zilele_vechi(tmp_path, monkeypatch):
    monkeypatch.setattr(reid_arhiva, "DIR", str(tmp_path))
    reid_arhiva._cache.clear()
    crop = np.zeros((100, 50, 3), np.uint8)
    reid_arhiva.adauga(1, 1, _v(3), crop, t=time.time() - 200 * 86400)
    reid_arhiva.adauga(1, 2, _v(3), crop, t=time.time())
    assert reid_arhiva.curata(90) == 1 and len(reid_arhiva.zile()) == 1
