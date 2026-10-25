"""Metricile per model (faza 6): cronometru ieftin, rata pe minut, latente."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import metrici  # noqa: E402


@pytest.fixture(autouse=True)
def curat(monkeypatch):
    monkeypatch.setattr(metrici, "_date", {})


def test_record_si_snapshot():
    metrici.record("face", 0.4, now=1000.0)
    metrici.record("face", 0.2, now=1010.0)
    s = metrici.snapshot(now=1010.0)["face"]
    assert s["apeluri"] == 2
    assert s["ms_mediu"] == 300
    assert s["ms_ultim"] == 200
    # 2 apeluri in fereastra de 300s -> 0.4/min
    assert s["pe_min"] == pytest.approx(0.4)


def test_rata_uita_apelurile_vechi():
    metrici.record("lpr", 0.1, now=1000.0)
    metrici.record("lpr", 0.1, now=2000.0)
    s = metrici.snapshot(now=2000.0)["lpr"]
    assert s["apeluri"] == 2                 # totalul ramane
    assert s["pe_min"] == pytest.approx(0.2)  # dar in fereastra e doar unul


def test_cronometru_masoara():
    with metrici.cronometru("detect"):
        pass
    s = metrici.snapshot()["detect"]
    assert s["apeluri"] == 1
    assert s["ms_ultim"] >= 0
