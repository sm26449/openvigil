"""Parola camerei nu iese din server prin erorile ffmpeg/ffprobe (health, joburi, jurnal)."""
import os
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import health  # noqa: E402
import logare  # noqa: E402


def test_fara_parole_curata_url_si_bytes():
    assert logare.fara_parole("x rtsp://admin:Secret%21@10.0.0.5:554/a: refused") == "x rtsp://***@10.0.0.5:554/a: refused"
    assert logare.fara_parole(b"http://u:p@h/") == "http://***@h/"
    assert logare.fara_parole("fara credentiale rtsp://10.0.0.5/a") == "fara credentiale rtsp://10.0.0.5/a"
    assert logare.fara_parole(None) == ""


def test_check_stream_nu_scurge_parola(monkeypatch):
    cam = {"id": 1, "ip": "10.0.0.5", "user": "admin", "pass": "Secret!", "port": 554}

    def run_fals(cmd, **kw):
        url = cmd[-1]
        assert "Secret" in url                      # ffprobe chiar primeste parola
        return subprocess.CompletedProcess(cmd, 1, b"", (url + ": Connection refused\n").encode())
    monkeypatch.setattr(health.subprocess, "run", run_fals)
    r = health.check_stream(cam)
    assert r["ok"] is False and "Secret" not in r["eroare"] and "***@" in r["eroare"]
