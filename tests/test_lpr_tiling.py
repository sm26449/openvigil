"""LPR declansat si din calea de tiling (detect.CamState._tile_job), nu doar din substream.

Camerele de strada vad vehiculele mai ales prin tiling (mici/departe); inainte LPR pornea DOAR
din `process` (substream), ratand majoritatea masinilor. Testul mockeaza modelul YOLO, cadrul HD
si executorii, si verifica submit-ul `_lpr_job` pe un vehicul PROASPAT prins prin tiling (cu
throttle pe ACTIVE_TTL si doar pentru etichete-vehicul).
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import detect  # noqa: E402


class _Box:
    def __init__(self, cls, xyxy, conf):
        self.cls = float(cls)
        self.xyxy = np.array([xyxy], dtype=float)
        self.conf = float(conf)


class _Res:
    def __init__(self, boxes):
        self.boxes = boxes

    def plot(self):
        return np.zeros((10, 10, 3), dtype=np.uint8)


class _Model:
    def __init__(self, res):
        self._res = res

    def predict(self, *a, **k):
        return [self._res]


@pytest.fixture
def harness(monkeypatch):
    """Mockeaza tot ce atinge _tile_job in afara logicii testate; intoarce lista de submit-uri."""
    submise = []

    monkeypatch.setattr(detect, "_ext_cam", lambda cam_id: {"user": "u", "pass": "p", "ip": "x"})
    monkeypatch.setattr(detect.faces, "hd_frame", lambda cam: np.zeros((600, 800, 3), dtype=np.uint8))
    monkeypatch.setattr(detect, "CLASSES", {2: "car", 0: "person"})
    monkeypatch.setattr(detect, "expand_region", lambda region, *a, **k: tuple(region))
    monkeypatch.setattr(detect, "chenare_json", lambda res: {})
    monkeypatch.setattr(detect, "amprenta", lambda: "test")
    monkeypatch.setattr(detect.snaps, "save_frame", lambda *a, **k: "poza.jpg")
    monkeypatch.setattr(detect.notify, "detection", lambda *a, **k: None)
    import hooks
    monkeypatch.setattr(hooks, "emit", lambda *a, **k: None)
    monkeypatch.setattr(detect.plates, "enabled", lambda: True)
    # gate comun: lasam totul sa treaca (fara zona/invatat/praguri)
    monkeypatch.setattr(detect.CamState, "_gate_comun", lambda self, box, ctx, img=None: None)

    class _Pool:
        def submit(self, fn, *a, **k):
            submise.append((fn, a))

    monkeypatch.setattr(detect, "_pools", lambda: (_Pool(), _Pool()))
    return submise


def _set_model(monkeypatch, *boxes):
    monkeypatch.setattr(detect, "_get_model", lambda: _Model(_Res(list(boxes))))


# o masina mica in crop: latura > TILE_MIN_PX dupa expand, arie in cadru < TILE_MAX_AREA
REGIUNE = (0.0, 0.0, 0.6, 0.85)
CAR = _Box(2, [10, 10, 40, 60], 0.9)       # ~30x50 px in crop -> arie plina ~0.003
PERSON = _Box(0, [10, 10, 40, 60], 0.9)


def test_vehicul_proaspat_din_tiling_declanseaza_lpr(harness, monkeypatch):
    _set_model(monkeypatch, CAR)
    cs = detect.CamState(14)
    cs._tile_job(REGIUNE)
    joburi = [fn.__name__ for fn, _ in harness]
    assert "_lpr_job" in joburi                       # vehicul proaspat -> LPR submis


def test_al_doilea_tiling_nu_redeclanseaza_sub_active_ttl(harness, monkeypatch):
    _set_model(monkeypatch, CAR)
    cs = detect.CamState(14)
    cs._tile_job(REGIUNE)
    harness.clear()
    cs._tile_job(REGIUNE)                             # imediat -> "car" inca activ (sub ACTIVE_TTL)
    assert "_lpr_job" not in [fn.__name__ for fn, _ in harness]


def test_non_vehicul_nu_declanseaza_lpr(harness, monkeypatch):
    _set_model(monkeypatch, PERSON)
    cs = detect.CamState(14)
    cs._tile_job(REGIUNE)
    assert "_lpr_job" not in [fn.__name__ for fn, _ in harness]
