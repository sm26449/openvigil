"""Orchestratorul regulii de cădere (app/cadere.py): pose și VLM injectate, deci fără model.
Verifică gating pe mărime, persistența (dwell), confirmarea VLM și emiterea evenimentului."""
import os
import sys

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
