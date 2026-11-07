"""Orchestratorul regulii de cădere (app/cadere.py): pose și VLM injectate, deci fără model.
Verifică gating pe mărime, persistența (dwell), confirmarea VLM și emiterea evenimentului."""
import os
import sys
import types

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import cadere as C  # noqa: E402
import reguli as R  # noqa: E402


# keypoints „în picioare" vs „culcat" (ca în test_reguli)
IN_PICIOARE = [(0.0, 0.0, 0.0)] * 17
IN_PICIOARE[R.UMAR_S] = (100, 100, 0.9); IN_PICIOARE[R.UMAR_D] = (140, 100, 0.9)
IN_PICIOARE[R.SOLD_S] = (105, 200, 0.9); IN_PICIOARE[R.SOLD_D] = (135, 200, 0.9)
IN_PICIOARE[R.NAS] = (120, 70, 0.9)

CULCAT = [(0.0, 0.0, 0.0)] * 17
CULCAT[R.UMAR_S] = (100, 150, 0.9); CULCAT[R.UMAR_D] = (100, 160, 0.9)
CULCAT[R.SOLD_S] = (260, 150, 0.9); CULCAT[R.SOLD_D] = (260, 160, 0.9)
CULCAT[R.NAS] = (70, 155, 0.9)

BBOX_OM = (0, 0, 300, 320)        # mare, trece de persoana_min_px
BBOX_MIC = (0, 0, 40, 40)


@pytest.fixture(autouse=True)
def _izolare(monkeypatch):
    vals = {"cadere_scor_prag": 0.6, "cadere_dwell_s": 6, "cadere_pose_conf": 0.5,
            "cadere_persoana_min_px": 60, "cadere_vlm": True}
    monkeypatch.setattr(C.settings, "get", lambda k: vals[k])
    C.reset()


def test_in_picioare_nu_declanseaza():
    emise = []
    for t in (0, 3, 6, 9):
        r = C.verifica(6, None, BBOX_OM, "t1", now=t,
                       keypoints_fn=lambda img, b: IN_PICIOARE, emit_fn=emise.append)
        assert r is None
    assert emise == []


def test_cadere_dupa_dwell_cu_vlm_confirmat():
    emise = []
    kp = lambda img, b: CULCAT                       # noqa: E731
    confirm = lambda img, b: True                    # noqa: E731
    assert C.verifica(6, None, BBOX_OM, "t1", now=0, keypoints_fn=kp, confirm_fn=confirm, emit_fn=emise.append) is None
    assert C.verifica(6, None, BBOX_OM, "t1", now=3, keypoints_fn=kp, confirm_fn=confirm, emit_fn=emise.append) is None
    ev = C.verifica(6, None, BBOX_OM, "t1", now=6, keypoints_fn=kp, confirm_fn=confirm, emit_fn=emise.append)
    assert ev and ev["cam"] == 6 and ev["scor"] > 0.6
    assert len(emise) == 1 and emise[0] is ev
    # nu reemite în același episod
    assert C.verifica(6, None, BBOX_OM, "t1", now=9, keypoints_fn=kp, confirm_fn=confirm, emit_fn=emise.append) is None


def test_vlm_infirma_nu_emite():
    emise = []
    kp = lambda img, b: CULCAT                       # noqa: E731
    C.verifica(6, None, BBOX_OM, "t1", now=0, keypoints_fn=kp, confirm_fn=lambda i, b: False, emit_fn=emise.append)
    r = C.verifica(6, None, BBOX_OM, "t1", now=6, keypoints_fn=kp, confirm_fn=lambda i, b: False, emit_fn=emise.append)
    assert r is None and emise == []


def test_vlm_esuat_lasa_sa_treaca():
    def boom(img, b):
        raise RuntimeError("VLM jos")
    kp = lambda img, b: CULCAT                       # noqa: E731
    C.verifica(6, None, BBOX_OM, "t1", now=0, keypoints_fn=kp, confirm_fn=boom)
    ev = C.verifica(6, None, BBOX_OM, "t1", now=6, keypoints_fn=kp, confirm_fn=boom)
    assert ev is not None                            # o cădere reală nu se pierde dacă VLM pică


def test_persoana_prea_mica_ignorata():
    r = C.verifica(6, None, BBOX_MIC, "t1", now=0, keypoints_fn=lambda i, b: CULCAT)
    assert r is None


def test_fara_keypoints_nimic():
    r = C.verifica(6, None, BBOX_OM, "t1", now=0, keypoints_fn=lambda i, b: None)
    assert r is None


def test_vlm_oprit_emite_direct(monkeypatch):
    vals = {"cadere_scor_prag": 0.6, "cadere_dwell_s": 6, "cadere_pose_conf": 0.5,
            "cadere_persoana_min_px": 60, "cadere_vlm": False}
    monkeypatch.setattr(C.settings, "get", lambda k: vals[k])
    C.reset()
    kp = lambda img, b: CULCAT                       # noqa: E731
    C.verifica(6, None, BBOX_OM, "t1", now=0, keypoints_fn=kp)
    ev = C.verifica(6, None, BBOX_OM, "t1", now=6, keypoints_fn=kp)   # fără confirm_fn
    assert ev is not None


# ---- glue-ul live ----
def test_severitate_cadere_critic():
    import rules
    assert rules.severity("cadere", {}, is_night=False) == "critic"
    assert rules.severity("cadere", {}, is_night=True) == "critic"


def test_confirma_si_emite_cu_vlm(monkeypatch):
    emise = []
    monkeypatch.setattr(C.settings, "get", lambda k: {"cadere_vlm": True}[k])
    monkeypatch.setattr(C, "_vlm_confirm", lambda f, b: True)
    ok = C.confirma_si_emite(6, None, (0, 0, 10, 10), {"scor": 0.7},
                             lambda tip, cam, date: emise.append((tip, cam, date)))
    assert ok is True and emise == [("cadere", 6, {"scor": 0.7})]
    emise.clear()
    monkeypatch.setattr(C, "_vlm_confirm", lambda f, b: False)
    assert C.confirma_si_emite(6, None, (0, 0, 10, 10), {"scor": 0.7},
                               lambda *a: emise.append(a)) is False
    assert emise == []


def test_confirma_vlm_esuat_lasa_sa_treaca(monkeypatch):
    emise = []
    monkeypatch.setattr(C.settings, "get", lambda k: {"cadere_vlm": True}[k])
    def boom(f, b):
        raise RuntimeError("VLM jos")
    monkeypatch.setattr(C, "_vlm_confirm", boom)
    assert C.confirma_si_emite(6, None, (0, 0, 10, 10), {"scor": 0.7},
                               lambda tip, cam, date: emise.append(1)) is True
    assert emise == [1]


def test_confirma_vlm_oprit_nu_cheama_vlm(monkeypatch):
    emise = []
    monkeypatch.setattr(C.settings, "get", lambda k: {"cadere_vlm": False}[k])
    monkeypatch.setattr(C, "_vlm_confirm",
                        lambda f, b: (_ for _ in ()).throw(AssertionError("nu chema VLM")))
    assert C.confirma_si_emite(6, None, (0, 0, 10, 10), {"scor": 0.7},
                               lambda tip, cam, date: emise.append(1)) is True
    assert emise == [1]


def test_vlm_confirm_parsing(monkeypatch):
    import describe
    frame = np.zeros((20, 20, 3), np.uint8)
    cazuri = [("Da", True), ("da, pare căzută", True), ("Nu.", False), ("nu", False),
              ("o persoană în picioare", False), ("lying on the ground", True),
              # negatii explicite: NU trebuie sa iasa pozitiv doar fiindca apare „cazut"/„fallen" (audit #8)
              ("nu, persoana nu este căzută", False),
              ("no, the person has not fallen", False),
              ("stă în picioare, nu a căzut", False),
              # negatii contractate engleze — „n't" trebuie recunoscut desi apare „fallen" (audit 4)
              ("the person hasn't fallen", False),
              ("she has not fallen", False),
              ("isn't lying on the ground", False),
              ("", False),                                  # raspuns gol -> nu escaladam
              ("persoana este căzută pe jos", True)]
    for ans, exp in cazuri:
        monkeypatch.setattr(describe, "describe_crop", lambda pil, prompt, a=ans: a)
        assert C._vlm_confirm(frame, (0, 0, 20, 20)) is exp, ans


def test_raspuns_cadere_direct():
    # parserul pur, fara decupaj/VLM
    assert C._raspuns_cadere("da") is True
    assert C._raspuns_cadere("nu, nu este căzută") is False
    assert C._raspuns_cadere("no, has not fallen") is False
    assert C._raspuns_cadere("the person hasn't fallen") is False
    assert C._raspuns_cadere("hasn't collapsed") is False
    assert C._raspuns_cadere("") is False
    assert C._raspuns_cadere("pare prăbușită pe jos") is True
    assert C._raspuns_cadere("persoana e așezată pe scaun") is False


def test_pas_pe_cadru_emite_dupa_dwell(monkeypatch):
    class FakeThread:                      # rulează ținta inline, determinist
        def __init__(self, target=None, args=(), **k):
            self._t, self._a = target, args
        def start(self):
            self._t(*self._a)
    monkeypatch.setattr(C.threading, "Thread", FakeThread)
    fake_pose = types.ModuleType("cadere_pose")
    fake_pose.keypoints = lambda img, b: CULCAT
    monkeypatch.setitem(sys.modules, "cadere_pose", fake_pose)
    monkeypatch.setattr(C, "_vlm_confirm", lambda f, b: True)
    vals = {"cadere_scor_prag": 0.6, "cadere_dwell_s": 6, "cadere_pose_conf": 0.5,
            "cadere_persoana_min_px": 60, "cadere_vlm": True}
    monkeypatch.setattr(C.settings, "get", lambda k: vals[k])
    C.reset(); C._last_pose.clear()
    frame = np.zeros((400, 400, 3), np.uint8)
    emise = []
    pers = [(7, (0, 0, 300, 320))]
    C.pas_pe_cadru(6, frame, pers, now=0, emit_fn=lambda tip, cam, d: emise.append((tip, cam, d)))
    C.pas_pe_cadru(6, frame, pers, now=3, emit_fn=lambda tip, cam, d: emise.append((tip, cam, d)))
    assert emise == []
    C.pas_pe_cadru(6, frame, pers, now=6, emit_fn=lambda tip, cam, d: emise.append((tip, cam, d)))
    assert len(emise) == 1 and emise[0][0] == "cadere" and emise[0][1] == 6 and "scor" in emise[0][2]
