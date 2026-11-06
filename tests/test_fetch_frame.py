"""detect._fetch_frame: un cadru de la go2rtc e intors DOAR daca e valid si de dimensiune
rezonabila (C4). go2rtc poate da 200 cu un corp non-JPEG/trunchiat cand fluxul e picat."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import detect  # noqa: E402


class _Resp:
    def __init__(self, ok, content):
        self.ok = ok
        self.content = content


def _jpeg(h, w):
    import cv2
    img = np.full((h, w, 3), 128, dtype=np.uint8)
    ok, buf = cv2.imencode(".jpg", img)
    assert ok
    return buf.tobytes()


def _mock(monkeypatch, resp):
    monkeypatch.setattr(detect.requests, "get", lambda *a, **k: resp)


def test_cadru_valid_e_intors(monkeypatch):
    _mock(monkeypatch, _Resp(True, _jpeg(64, 64)))
    img = detect._fetch_frame(6)
    assert img is not None and img.shape[0] == 64 and img.shape[1] == 64


def test_corp_non_jpeg_da_none(monkeypatch):
    _mock(monkeypatch, _Resp(True, b"<html>eroare go2rtc</html>"))   # imdecode -> None
    assert detect._fetch_frame(6) is None


def test_cadru_minuscul_da_none(monkeypatch):
    _mock(monkeypatch, _Resp(True, _jpeg(16, 16)))                   # sub 32x32 -> respins
    assert detect._fetch_frame(6) is None


def test_raspuns_neok_da_none(monkeypatch):
    _mock(monkeypatch, _Resp(False, _jpeg(64, 64)))
    assert detect._fetch_frame(6) is None


def test_corp_gol_da_none(monkeypatch):
    _mock(monkeypatch, _Resp(True, b""))
    assert detect._fetch_frame(6) is None
