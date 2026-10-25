"""antrenare.py: parsarea progresului si un job de proba cap-coada (subproces real)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import antrenare  # noqa: E402


def test_parseaza_progres():
    st = {"progres": 0, "total": 0, "mesaj": "", "rezultat": None}
    assert antrenare.parseaza("[dataset] 12/400", st) and (st["progres"], st["total"]) == (12, 400)
    assert antrenare.parseaza("[train] epoca 3/30 mAP50=0.812", st)
    assert st["mesaj"].startswith("epoca 3/30") and "0.812" in st["mesaj"]
    assert antrenare.parseaza("[train] gata best=/x/best.pt", st) and st["rezultat"] == "/x/best.pt"
    assert antrenare.parseaza("      1/30  6.2G  0.9 ...", st) is False


def test_nume_urmator(tmp_path, monkeypatch):
    monkeypatch.setattr(antrenare, "FINETUNE", str(tmp_path))
    assert antrenare.nume_urmator() == "curte-v1"
    (tmp_path / "runs" / "curte-v1").mkdir(parents=True)
    (tmp_path / "runs" / "curte-v7").mkdir()
    (tmp_path / "runs" / "altceva").mkdir()
    assert antrenare.nume_urmator() == "curte-v8"


def test_job_de_proba_cap_coada():
    antrenare.stare.update(activ=False)
    s = antrenare.porneste("proba")
    assert s["activ"] and s["tip"] == "proba"
    try:
        antrenare.porneste("proba")
        assert False, "al doilea job ar fi trebuit refuzat"
    except RuntimeError:
        pass
    for _ in range(100):
        if not antrenare.stare["activ"]:
            break
        time.sleep(0.1)
    c = antrenare.curent()
    assert not c["activ"] and c["rc"] == 0
    assert (c["progres"], c["total"]) == (3, 3) and c["rezultat"] == "/tmp/x.pt"
    assert any("epoca 2/3" in l for l in c["log"])
