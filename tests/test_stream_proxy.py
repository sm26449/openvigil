"""Releul de flux (app/stream_proxy.py): ce fluxuri primeste fiecare rol (audit S1, S2, S7)."""
import os
import socket
import sys
import threading
import time

import pytest
from flask import Flask, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import stream_proxy  # noqa: E402

CONTURI = {"v": {"user": "v", "role": "viewer"}, "o": {"user": "o", "role": "operator"},
           "a": {"user": "a", "role": "admin"}}
VIZIBILE = {"v": {1}, "o": {1}, "a": None}


@pytest.fixture
def px():
    app = Flask(__name__)
    stare = {"conturi": dict(CONTURI)}

    def cine():
        return stare["conturi"].get(request.cookies.get("u", ""))

    def vizibile():
        u = cine()
        return VIZIBILE.get(u["user"]) if u else None

    p = stream_proxy.StreamProxy(app, cine, vizibile, ("127.0.0.1", 1), "127.0.0.1", 0,
                                 are_vorbire=lambda cid: cid == 1)
    p.stare = stare
    return p


def ws(px, u, src, cale="/api/ws"):
    hdr = {"cookie": f"u={u}", "upgrade": "websocket", "host": "nvr:8076"}
    return px._decide("GET", f"{cale}?src={src}", hdr)


@pytest.mark.parametrize("src", ["cam1", "hd1", "hd1_h720", "hd1_h0", "pb1_20260601T100000Z_1",
                                 "pb1_20260601T100000Z_05_h1080", "pb1_20260601T100000Z"])
def test_operator_vede_fluxurile_publicate(px, src):
    assert ws(px, "o", src) == f"/api/ws?src={src}"


@pytest.mark.parametrize("src", ["cam1_main", "cam1_v", "cam1x", "hd1_h480", "pb1_x", "m1", "cam1%20", "cam2"])
def test_fluxurile_interne_si_alte_camere_sunt_refuzate(px, src):
    r = ws(px, "o", src)
    assert isinstance(r, tuple) and r[0] == 403


def test_viewerul_primeste_live_fara_canal_de_vorbire(px):
    assert ws(px, "v", "cam1") == "/api/ws?src=cam1_v"           # camera 1 are difuzor
    assert ws(px, "o", "cam1") == "/api/ws?src=cam1"             # operatorul poate vorbi
    assert ws(px, "v", "hd1_h720") == "/api/ws?src=hd1_h720"     # HD-ul n-are canal de vorbire


def test_adminul_nu_ajunge_la_administrarea_go2rtc(px):
    h = {"cookie": "u=a", "host": "nvr:8076"}
    for cale in ("/api/config", "/api/restart", "/api/exit", "/api/streams", "/api/log", "/", "/editor.html"):
        assert px._decide("GET", cale, h)[0] == 403, cale
    assert px._decide("POST", "/api/config", h)[0] == 403
    assert px._decide("GET", "/api/frame.jpeg?src=cam3", h) == "/api/frame.jpeg?src=cam3"
    assert ws(px, "a", "cam7") == "/api/ws?src=cam7"             # adminul vede toate camerele
    assert ws(px, "a", "cam7_main")[0] == 403                    # dar nu fluxul brut intern
    assert ws(px, "a", "cam1") == "/api/ws?src=cam1"             # si poate vorbi
    assert ws(px, "a", "cam1&src=cam1_main")[0] == 403           # doua surse = refuz


def test_fara_sesiune_si_origine_straina(px):
    assert ws(px, "nimeni", "cam1")[0] == 401
    hdr = {"cookie": "u=o", "upgrade": "websocket", "host": "nvr:8076", "origin": "https://rau.example"}
    assert px._decide("GET", "/api/ws?src=cam1", hdr)[0] == 403
    assert px._decide("GET", "/api/frame.jpeg?src=cam1", {"cookie": "u=o", "host": "x"})[0] == 403


def test_fluxul_deschis_se_inchide_la_revocare(px, monkeypatch):
    monkeypatch.setattr(stream_proxy, "REVERIFICA", 0.05)
    a, b = socket.socketpair()
    c, d = socket.socketpair()
    gata = threading.Event()
    hdr = {"cookie": "u=o", "upgrade": "websocket", "host": "nvr:8076"}
    t = threading.Thread(target=px._paznic, args=(gata, a, c, "GET", "/api/ws?src=cam1", hdr), daemon=True)
    t.start()
    time.sleep(0.15)
    assert a.fileno() != -1                                      # inca autorizat: ramane deschis
    del px.stare["conturi"]["o"]                                 # contul sters / sesiune revocata
    t.join(1)
    assert not t.is_alive() and a.fileno() == -1 and c.fileno() == -1
    for s in (b, d):
        s.close()



def test_rezerva_hls_merge_pentru_orice_cont(px):
    """Safari/iOS: dupa WebSocket, playerul cere playlist-ul si segmentele HLS prin GET simplu."""
    h = {"cookie": "u=v", "host": "nvr:8076"}
    for cale in ("/api/hls/playlist.m3u8?id=ab12", "/api/hls/segment.ts?id=ab12&n=3",
                 "/api/hls/init.mp4?id=ab12", "/api/hls/segment.m4s?id=ab12&n=1"):
        assert px._decide("GET", cale, h) == cale
    assert px._decide("GET", "/api/hls/playlist.m3u8", h)[0] == 403           # fara id
    assert px._decide("GET", "/api/hls/../config?id=x", h)[0] == 403
    assert px._decide("GET", "/api/hls/playlist.m3u8?id=x", {"cookie": "", "host": "x"})[0] == 401
