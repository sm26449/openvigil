"""Restrictia pe camere (app/scoping.py): id-ul camerei din cale, query sau corp JSON, si
decizia pentru un cont restrans. Plus: TOATE rutele din cod care au o camera in cale sunt
recunoscute (altfel o ruta noua ar scapa de restrictie, exact bugul din audit)."""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import scoping  # noqa: E402

APP = os.path.join(os.path.dirname(__file__), "..", "app")


def test_cam_din_cale_query_si_corp():
    f = scoping.cam_din_cerere
    assert f("/api/cam/7/siren") == 7 and f("/api/cam/7") == 7 and f("/api/thumb/12") == 12
    assert f("/api/cameras/101") == 101 and f("/api/cameras") is None
    assert f("/api/play", {"channel": "5"}) == 5 and f("/api/marcaje", {"cam": "9"}) == 9
    assert f("/api/regiune", {"channel": "x"}) is None
    assert f("/api/marcaje", {}, {"cam": 3}) == 3 and f("/api/cases", {}, {"camera": "4"}) == 4
    assert f("/api/mod", {}, {"mod": "acasa"}) is None and f("/api/me/2fa/activeaza", {}, None) is None


def test_decizia_pentru_cont_restrans():
    vis = {5, 6}
    assert scoping.permisa("/api/cam/5/ptz", {}, {}, vis) and not scoping.permisa("/api/cam/9/ptz", {}, {}, vis)
    assert scoping.permisa("/api/play", {"channel": "6"}, None, vis) and not scoping.permisa("/api/play", {"channel": "7"}, None, vis)
    assert not scoping.permisa("/api/marcaje", {}, {"cam": 7, "start": "x"}, vis)
    assert scoping.permisa("/api/incidents", {}, None, vis)          # fara camera: ruta filtreaza
    assert scoping.permisa("/api/cam/9/ptz", {}, {}, None)          # None = toate camerele


def test_toate_rutele_cu_camera_in_cale_sunt_recunoscute():
    rute = []
    for fn in os.listdir(APP):
        if fn.endswith(".py"):
            for m in re.finditer(r'@(?:bp|app)\.route\("([^"]+)"', open(os.path.join(APP, fn)).read()):
                rute.append(m.group(1))
    cu_camera = [r for r in rute if "<int:cam_id>" in r]
    assert len(cu_camera) >= 15
    for r in cu_camera:
        cale = r.replace("<int:cam_id>", "42").split("<")[0]
        assert scoping.cam_din_cerere(cale) == 42, f"ruta nescopata: {r}"
