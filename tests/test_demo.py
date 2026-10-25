"""Modul demo: camere sintetice (vendor „demo”) — surse go2rtc lavfi, fara actiuni, fara retea."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import drivers  # noqa: E402

CAM = {"id": 7, "vendor": "demo", "demo": "smptebars", "ip": "demo-smptebars", "user": "", "pass": "", "name": "Garaj (demo)"}


def test_sursele_sunt_sintetice_si_diferite_pe_marime():
    assert drivers.rtsp_main(CAM) == "ffmpeg:virtual?video=smptebars&size=1280x720&rate=15#video=h264"
    assert drivers.rtsp_sub(CAM).startswith("ffmpeg:virtual?video=smptebars&size=640x360")
    # tipar necunoscut -> cel implicit, nu o comanda arbitrara catre ffmpeg
    assert "video=testsrc2&" in drivers.rtsp_main({**CAM, "demo": "; rm -rf /"})


def test_fara_retea_si_fara_actiuni():
    assert drivers.ping(CAM) is True
    assert drivers.snapshot(CAM) is None
    assert drivers.device_info(CAM)["model"] == "Demo"
    assert drivers.are_evenimente(CAM) is False
    f = drivers.probe_features(CAM)
    assert not any(v for k, v in f.items() if k not in ("sounds", "smart", "io_out"))
    with pytest.raises(drivers.Nesuportat):
        drivers.ptz_move(CAM, pan=1)
    with pytest.raises(drivers.Nesuportat):
        drivers.trigger_siren(CAM)


def test_vendorul_demo_e_cunoscut():
    assert "demo" in drivers.VENDORI and drivers.vendor(CAM) == "demo"
