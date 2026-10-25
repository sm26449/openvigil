"""Re-ID pe corp (OSNet): galeria zilei, ancorarea fata->corp, esantionarea din
bucla de urmarire. Modelul ONNX nu se atinge — embed() se inlocuieste."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import episoade  # noqa: E402
import reid  # noqa: E402
import track  # noqa: E402


def _unit(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


@pytest.fixture(autouse=True)
def _galerie_curata(monkeypatch):
    monkeypatch.setattr(reid, "_galerie", {"zi": "", "persoane": {}})
    monkeypatch.setattr(reid, "_azi", lambda: "2026-09-27")


# ---- galeria zilei ----

def test_cine_peste_si_sub_prag():
    reid.ancora("stefan", [_unit([1, 0, 0])])
    nume, sim = reid.cine(_unit([1, 0.1, 0]))
    assert nume == "stefan" and sim > reid.PRAG
    nume, sim = reid.cine(_unit([0, 0, 1]))
    assert nume is None and sim < 0.1


def test_cine_alege_cel_mai_apropiat():
    reid.ancora("stefan", [_unit([1, 0, 0])])
    reid.ancora("maria", [_unit([0, 1, 0])])
    assert reid.cine(_unit([0.2, 1, 0]))[0] == "maria"


def test_galeria_se_goleste_la_schimbarea_zilei(monkeypatch):
    reid.ancora("stefan", [_unit([1, 0, 0])])
    assert reid.snapshot()["persoane"] == {"stefan": 1}
    monkeypatch.setattr(reid, "_azi", lambda: "2026-09-28")   # haine noi, galerie noua
    assert reid.cine(_unit([1, 0, 0]))[0] is None
    assert reid.snapshot() == {"zi": "2026-09-28", "persoane": {}}


def test_galeria_se_roteste_la_plafon():
    reid.ancora("stefan", [_unit([1, 0, 0])] * (reid.MAX_GALERIE + 10))
    assert reid.snapshot()["persoane"]["stefan"] == reid.MAX_GALERIE


# ---- ancorarea fata -> corp ----

class _FakeTracker:
    def __init__(self, tracks):
        self.tracks = tracks


def _pregatit(monkeypatch, tracks):
    monkeypatch.setattr(reid, "activ", lambda: True)
    monkeypatch.setattr(reid, "_tracker", lambda cam: _FakeTracker(tracks))


def test_ancoreaza_un_singur_om(monkeypatch):
    t = {"lbl": "person", "reid": [_unit([1, 0, 0])]}
    _pregatit(monkeypatch, {1: t})
    assert reid.ancoreaza(7, "stefan")
    assert t["cine"] == "stefan" and t["cine_sursa"] == "fata"
    assert reid.snapshot()["persoane"] == {"stefan": 1}


def test_ancoreaza_refuza_doi_oameni(monkeypatch):
    doi = {1: {"lbl": "person", "reid": []}, 2: {"lbl": "person", "reid": []}}
    _pregatit(monkeypatch, doi)
    assert not reid.ancoreaza(7, "stefan")            # ambiguu: nu ghicim
    assert reid.snapshot()["persoane"] == {}


def test_ancoreaza_fara_embeddings_pune_doar_numele(monkeypatch):
    t = {"lbl": "person"}
    _pregatit(monkeypatch, {1: t})
    assert reid.ancoreaza(7, "stefan")
    assert t["cine"] == "stefan"
    assert reid.snapshot()["persoane"] == {}          # galeria se umple la esantionare


# ---- esantionarea din bucla de urmarire (job sincron in teste) ----

@pytest.fixture
def tracker(monkeypatch, tmp_path):
    monkeypatch.setattr(reid, "activ", lambda: True)
    monkeypatch.setattr(reid, "_submit", lambda fn, *a: fn(*a))   # fara executor
    import reid_arhiva
    monkeypatch.setattr(reid_arhiva, "DIR", str(tmp_path / "reid"))   # arhiva de corpuri: in tmp, nu in data/
    monkeypatch.setattr(reid, "_hd_frame", lambda cam: None)      # fallback substream
    return track.Tracker(7)


def _om(tracker, tid=1, nbox=(0.4, 0.2, 0.15, 0.5), now=1000.0):
    for i in range(track.MIN_FRAMES):
        tracker.update([(tid, "person", 320, 240, nbox)], now + i * 0.25)
    return tracker.tracks[tid]


def test_reid_sample_esantioneaza_si_da_nume(tracker, monkeypatch):
    frame = np.zeros((480, 640, 3), np.uint8)
    monkeypatch.setattr(reid, "embed", lambda crops: np.stack([_unit([1, 0, 0])] * len(crops)))
    reid.ancora("stefan", [_unit([1, 0, 0])])
    t = _om(tracker)
    tracker.reid_sample(frame, 1002.0)
    assert t["cine"] == "stefan" and t["cine_sursa"] == "reid"
    assert len(t["reid"]) == 1
    # sub PAS secunde nu re-esantioneaza
    tracker.reid_sample(frame, 1002.0 + reid.PAS - 1)
    assert len(t["reid"]) == 1
    tracker.reid_sample(frame, 1002.0 + reid.PAS + 1)
    assert len(t["reid"]) == 2
    assert not tracker._reid_busy       # jobul sincron a eliberat camera


def test_reid_sample_prefera_cadrul_hd(tracker, monkeypatch):
    # HD 1520p: acelasi nbox normalizat -> crop cu mult mai multi pixeli
    hd = np.zeros((1520, 2688, 3), np.uint8)
    monkeypatch.setattr(reid, "_hd_frame", lambda cam: hd)
    marimi = []
    monkeypatch.setattr(reid, "embed",
                        lambda crops: (marimi.extend(c.shape[0] for c in crops),
                                       np.stack([_unit([1, 0, 0])] * len(crops)))[1])
    _om(tracker)
    tracker.reid_sample(np.zeros((480, 640, 3), np.uint8), 1002.0)
    assert marimi and marimi[0] > 700   # 0.5 * 1520 (+margine) — crop din HD, nu 240px

def test_reid_sample_track_ancorat_hraneste_galeria(tracker, monkeypatch):
    frame = np.zeros((480, 640, 3), np.uint8)
    monkeypatch.setattr(reid, "embed", lambda crops: np.stack([_unit([0, 1, 0])] * len(crops)))
    t = _om(tracker)
    t["cine"], t["cine_sursa"] = "maria", "fata"
    tracker.reid_sample(frame, 1002.0)
    assert reid.snapshot()["persoane"] == {"maria": 1}
    assert reid.cine(_unit([0, 1, 0]))[0] == "maria"


def test_reid_sample_ignora_crop_mic(tracker, monkeypatch):
    frame = np.zeros((480, 640, 3), np.uint8)
    monkeypatch.setattr(reid, "embed",
                        lambda crops: (_ for _ in ()).throw(AssertionError("nu trebuia")))
    t = _om(tracker, nbox=(0.4, 0.2, 0.02, 0.1))      # 48px pe substream < MIN_H_PX_SUB
    tracker.reid_sample(frame, 1002.0)
    assert "reid" not in t
    assert not tracker._reid_busy


def test_reid_sample_camera_ocupata_nu_dubleaza(tracker, monkeypatch):
    monkeypatch.setattr(reid, "embed",
                        lambda crops: (_ for _ in ()).throw(AssertionError("nu trebuia")))
    tracker._reid_busy = True                          # jobul anterior inca ruleaza
    _om(tracker)
    tracker.reid_sample(np.zeros((480, 640, 3), np.uint8), 1002.0)
    assert "reid" not in tracker.tracks[1]


def test_reid_sample_oprit_nu_face_nimic(tracker, monkeypatch):
    monkeypatch.setattr(reid, "activ", lambda: False)
    _om(tracker)
    tracker.reid_sample(np.zeros((480, 640, 3), np.uint8), 1002.0)
    assert "reid" not in tracker.tracks[1]


# ---- ancorarea din cadrul fetei ----

def test_ancora_din_cadru_alege_corpul_care_contine_fata(monkeypatch):
    import detect
    monkeypatch.setattr(reid, "activ", lambda: True)
    monkeypatch.setattr(reid, "_tracker", lambda cam: None)
    # doi oameni in cadru: fata (centrata la x=200) e in primul box
    monkeypatch.setattr(detect, "persoane",
                        lambda img: [[150, 100, 300, 500], [400, 120, 560, 520]])
    monkeypatch.setattr(reid, "embed", lambda crops: np.stack([_unit([1, 0, 0])]))
    img = np.zeros((720, 1280, 3), np.uint8)
    assert reid.ancoreaza(101, "stefan", img=img, bbox=(180, 120, 220, 170))
    assert reid.snapshot()["persoane"] == {"stefan": 1}
    assert reid.cine(_unit([1, 0, 0]))[0] == "stefan"


def test_ancora_din_cadru_fata_fara_corp_nu_ancoreaza(monkeypatch):
    import detect
    monkeypatch.setattr(reid, "activ", lambda: True)
    monkeypatch.setattr(reid, "_tracker", lambda cam: None)
    monkeypatch.setattr(detect, "persoane", lambda img: [[400, 120, 560, 520]])
    img = np.zeros((720, 1280, 3), np.uint8)
    assert not reid.ancoreaza(101, "stefan", img=img, bbox=(180, 120, 220, 170))
    assert reid.snapshot()["persoane"] == {}


def test_ancora_din_cadru_corp_prea_mic(monkeypatch):
    import detect
    monkeypatch.setattr(reid, "activ", lambda: True)
    monkeypatch.setattr(reid, "_tracker", lambda cam: None)
    monkeypatch.setattr(detect, "persoane", lambda img: [[190, 130, 230, 210]])  # 80px
    img = np.zeros((720, 1280, 3), np.uint8)
    assert not reid.ancoreaza(101, "stefan", img=img, bbox=(195, 135, 225, 175))
    assert reid.snapshot()["persoane"] == {}


# ---- numele re-ID intra in episod ----

def test_numele_reid_intra_in_rezumatul_episodului(monkeypatch):
    out = []
    import hooks
    monkeypatch.setattr(hooks, "emit", lambda tip, cam=None, **d: out.append((tip, d)))
    monkeypatch.setattr(episoade, "_ep", {})
    monkeypatch.setattr(episoade, "_nume", lambda cam, t0, t1: [])
    monkeypatch.setattr(episoade, "_semnalmente", lambda cam, t0, t1: None)
    monkeypatch.setattr(episoade.ai_analytics, "zones", lambda cid: [])
    episoade.cadru(7, [(0.5, 0.5)], 1000.0)
    episoade.track_inchis(7, "stanga→dreapta", 1010.0, cine="stefan")
    episoade.tick(7, 1010.0 + episoade.GAP + 1)
    assert len(out) == 1
    d = out[0][1]
    assert d["nume"] == ["stefan"]
    assert "stefan" in d["text"]
