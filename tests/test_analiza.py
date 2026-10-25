"""Analitica retroactiva (app/analiza.py): cautare pe regiune, harta de caldura, time-lapse."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import analiza  # noqa: E402
import jurnal  # noqa: E402
import record  # noqa: E402


_n = [0]


def _ev(tip, cam, t, **d):
    _n[0] += 1
    jurnal.insert({"id": f"{tip}{_n[0]}", "t": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)),
                   "tip": tip, "sev": "info", "cam": cam, **d}, ts=t)


def test_intersectie():
    assert analiza.se_ating([0, 0, 0.5, 0.5], [0.4, 0.4, 0.2, 0.2])
    assert not analiza.se_ating([0, 0, 0.5, 0.5], [0.5, 0.5, 0.2, 0.2])   # doar se ating
    assert not analiza.se_ating([0, 0, 0.1, 0.1], [0.9, 0.9, 0.1, 0.1])


def test_cautare_pe_regiune():
    zi = time.strftime("%Y-%m-%d")
    t0 = time.mktime(time.strptime(zi + " 10:00:00", "%Y-%m-%d %H:%M:%S"))
    _ev("detectie", 5, t0, eticheta="person", chenar=[0.1, 0.1, 0.2, 0.2])
    _ev("obiect", 5, t0 + 60, eticheta="car", chenar=[0.7, 0.7, 0.2, 0.2])
    _ev("detectie", 5, t0 + 120, eticheta="dog")                  # fara chenar: ignorat
    _ev("detectie", 6, t0 + 180, eticheta="person", chenar=[0.1, 0.1, 0.2, 0.2])  # alta camera
    _ev("episod", 5, t0 + 240, chenar=[0.1, 0.1, 0.2, 0.2])        # tip fara chenar relevant
    r = analiza.regiune(5, zi, [0.0, 0.0, 0.25, 0.25])
    assert [x["eticheta"] for x in r] == ["person"] and r[0]["sec"] == 36000
    r = analiza.regiune(5, zi, [0.0, 0.0, 1, 1])
    assert sorted(x["tip"] for x in r) == ["detectie", "obiect"]
    with pytest.raises(ValueError):
        analiza.regiune(5, zi, [0, 0, 0, 0])


def test_harta_de_caldura():
    zi = time.strftime("%Y-%m-%d")
    t0 = time.mktime(time.strptime(zi + " 12:00:00", "%Y-%m-%d %H:%M:%S"))
    _ev("episod", 5, t0, traseu=[[0.0, 0.0], [0.01, 0.01], [0.99, 0.99]])
    _ev("episod", 5, t0 - 3 * 86400, traseu=[[0.0, 0.0]])          # acum 3 zile
    _ev("episod", 7, t0, traseu=[[0.5, 0.5]])                      # alta camera
    h = analiza.heatmap(5, zi, 1)
    assert h["episoade"] == 1 and h["grid"][0][0] == 2 and h["grid"][35][63] == 1 and h["max"] == 2
    h7 = analiza.heatmap(5, zi, 7)
    assert h7["episoade"] == 2 and h7["grid"][0][0] == 3


def test_cadre_si_comenzi_timelapse():
    zi = "2026-10-20"
    t = time.mktime(time.strptime(zi + " 08:00:10", "%Y-%m-%d %H:%M:%S"))
    seg = [(t, "/rec/a.mp4"), (t + 300, "/rec/b.mp4")]
    c = analiza.cadre(seg, pas=60)
    # aliniat la minut: 08:01:00 (decalaj 50), 08:02 ... pana la 08:05 (in a), apoi b
    assert c[0] == ("/rec/a.mp4", 50.0) and c[4] == ("/rec/a.mp4", 290.0)
    assert c[5] == ("/rec/b.mp4", 50.0) and len(c) == 10
    assert analiza.cadre(seg, pas=300) == [("/rec/a.mp4", 290.0), ("/rec/b.mp4", 290.0)]
    cmd = analiza.comanda_cadru("/rec/a.mp4", 50.0, "/x/00001.jpg")
    assert cmd[cmd.index("-ss") + 1] == "50.0" and cmd[-1] == "/x/00001.jpg" and "scale=-2:720" in cmd
    enc = analiza.comanda_timelapse("/c", "/o.mp4", hw=True, masca_vf="drawbox=x=0")
    assert enc[enc.index("-i") + 1] == "/c/%05d.jpg" and "h264_nvenc" in enc and enc[enc.index("-vf") + 1] == "drawbox=x=0"
    assert "-vf" not in analiza.comanda_timelapse("/c", "/o.mp4", hw=False)


def test_job_timelapse(tmp_path, monkeypatch):
    zi = "2026-10-20"
    t = time.mktime(time.strptime(zi + " 08:00:00", "%Y-%m-%d %H:%M:%S"))
    monkeypatch.setattr(record, "_files", lambda cam: [(t, "/rec/a.mp4", "a"), (t + 300, "/rec/b.mp4", "b"),
                                                       (t + 86400, "/rec/c.mp4", "c")])
    assert [p for _, p in analiza.segmente_zi(5, zi)] == ["/rec/a.mp4", "/rec/b.mp4"]
    assert analiza.segmente_zi(5, "2026-01-01") == []

    class Job:
        parametri = {"channel": 5, "day": zi, "pas": 60}
        anulat = False
        def __init__(self): self.msg = []; self.f = None
        def progres(self, p, m=""): self.msg.append((p, m))
        def cale(self, ext): return str(tmp_path / f"j.{ext}")
        def fisier(self, cale, nume, mime): self.f = (cale, nume, mime)
    extrase = []
    def run_fals(cmd, capture_output=True, timeout=30):
        extrase.append(cmd); open(cmd[-1], "wb").write(b"jpg")
    vazut = {}
    def ffmpeg_fals(cmd, durata=None, timeout=600, job=None):
        vazut["cmd"] = cmd; open(cmd[-1], "wb").write(b"x" * 2000); return 0, "", False
    j = Job()
    r = analiza.job_timelapse(j, ffmpeg_fals, hw=False, masca_vf="drawbox=x=0", run=run_fals)
    assert r["segmente"] == 2 and r["cadre"] == 10 and len(extrase) == 10
    assert j.f[1] == "cam5_2026-10-20_timelapse.mp4" and vazut["cmd"][vazut["cmd"].index("-vf") + 1] == "drawbox=x=0"
    assert not os.path.exists(str(tmp_path / "j.cadre"))             # cadrele temporare sterse
    with pytest.raises(RuntimeError):
        Job.parametri = {"channel": 5, "day": "2026-01-01"}
        analiza.job_timelapse(Job(), ffmpeg_fals, hw=False, run=run_fals)
