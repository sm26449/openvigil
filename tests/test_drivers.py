"""Stratul de drivere (app/drivers.py): alegerea producatorului, URL-urile RTSP, ce e
nesuportat pe ONVIF — fara nicio camera reala."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import drivers  # noqa: E402

HIK = {"id": 5, "ip": "10.0.0.5", "user": "admin", "pass": "p@ss/1", "port": 554}
ONV = {"id": 101, "ip": "10.0.0.9", "user": "u", "pass": "p w", "vendor": "onvif",
       "onvif": {"main": "rtsp://10.0.0.9:554/live/ch0", "sub": "rtsp://10.0.0.9:554/live/ch1",
                 "poza": "http://10.0.0.9/snap.jpg", "port": 8080}}


def test_vendor_implicit_si_valid():
    assert drivers.vendor({}) == "hik" and drivers.vendor({"vendor": "x"}) == "hik"
    assert drivers.vendor(ONV) == "onvif" and drivers.este_hik(HIK) and not drivers.este_hik(ONV)
    assert drivers.are_evenimente(HIK) and not drivers.are_evenimente(ONV)


def test_rtsp_hikvision():
    assert drivers.rtsp_main(HIK) == "rtsp://admin:p%40ss%2F1@10.0.0.5:554/Streaming/Channels/101"
    assert drivers.rtsp_sub(HIK).endswith("/Streaming/Channels/102")
    assert drivers.rtsp(HIK, 101) == drivers.rtsp_main(HIK) and drivers.rtsp(HIK, 102) == drivers.rtsp_sub(HIK)


def test_rtsp_onvif_cu_parola_injectata():
    assert drivers.rtsp_main(ONV) == "rtsp://u:p%20w@10.0.0.9:554/live/ch0"
    assert drivers.rtsp_sub(ONV) == "rtsp://u:p%20w@10.0.0.9:554/live/ch1"
    # URL-ul care are deja credentiale ramane neatins
    c = {**ONV, "onvif": {"main": "rtsp://a:b@10.0.0.9/x"}}
    assert drivers.rtsp_main(c) == "rtsp://a:b@10.0.0.9/x" and drivers.rtsp_sub(c) == "rtsp://a:b@10.0.0.9/x"
    with pytest.raises(drivers.Nesuportat):
        drivers.rtsp_main({**ONV, "onvif": {}})


def test_nesuportat_pe_onvif():
    for fn in (drivers.trigger_siren, drivers.set_strobe, drivers.speak, drivers.set_time):
        with pytest.raises(drivers.Nesuportat):
            fn(ONV)
    with pytest.raises(drivers.Nesuportat):
        drivers.set_stream(ONV, 101, kbps=1)
    assert drivers.get_stream(ONV) == {} and drivers.setup_camera(ONV, {}) is None
