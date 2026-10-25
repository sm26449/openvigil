"""Teste pentru app/ai_scene.py — detectorul de obiect nou/disparut pe camere statice.
Cadre sintetice + timp simulat (feed primeste `now` explicit)."""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ai_scene  # noqa: E402

S = ai_scene.SAMPLE_SEC


def bg(val=120, h=480, w=640):
    return np.full((h, w, 3), val, np.uint8)


def with_obj(val_bg=120, x=400, y=300, s=30, val=235):
    f = bg(val_bg)
    f[y:y + s, x:x + s] = val
    return f


def _run(w, frames_fn, boxes_fn, n, t0):
    """Ruleaza n mostre, avansand timpul cu SAMPLE_SEC; intoarce evenimentele adunate."""
    ev, t = [], t0
    for i in range(n):
        ev += w.feed(frames_fn(i), boxes_fn(i), t)
        t += S
    return ev, t


def test_obiect_nou_dupa_persistenta():
    w = ai_scene.SceneWatcher(14, persist_sec=60)
    _, t = _run(w, lambda i: bg(), lambda i: [], 8, 1000.0)      # fundal -> referinta
    ev, _ = _run(w, lambda i: with_obj(), lambda i: [], 30, t)   # apare si ramane
    nou = [e for e in ev if e["tip"] == "obiect_nou"]
    assert nou, "obiectul lasat trebuie raportat ca obiect_nou"
    ux, uy = map(int, nou[0]["date"]["unde"].split(","))
    assert 55 < ux < 75 and 55 < uy < 75                         # ~centrul patratului
    assert nou[0]["date"]["de_minute"] >= 1


def test_obiect_in_banda_de_sus_ignorat():
    # un obiect aparut in top-ul cadrului (cer/copaci pe camerele de curte) = fals-pozitiv
    w = ai_scene.SceneWatcher(14, persist_sec=60, ignore_top=0.35)
    _, t = _run(w, lambda i: bg(), lambda i: [], 8, 1000.0)
    ev, _ = _run(w, lambda i: with_obj(y=40), lambda i: [], 30, t)   # y=40/480 ~ 8% (sus)
    assert not ev, "schimbarile din banda de sus (ROI) trebuie ignorate"


def test_nu_raporteaza_inainte_de_prag():
    w = ai_scene.SceneWatcher(14, persist_sec=600)               # prag mare
    _, t = _run(w, lambda i: bg(), lambda i: [], 8, 1000.0)
    ev, _ = _run(w, lambda i: with_obj(), lambda i: [], 10, t)   # doar ~200s < 600s
    assert not ev


def test_obiect_disparut():
    w = ai_scene.SceneWatcher(14, persist_sec=60)
    _, t = _run(w, lambda i: with_obj(), lambda i: [], 8, 1000.0)  # fundal CU obiect
    ev, _ = _run(w, lambda i: bg(), lambda i: [], 30, t)           # dispare
    assert any(e["tip"] == "obiect_disparut" for e in ev)


def test_obiect_in_miscare_ignorat():
    w = ai_scene.SceneWatcher(14, persist_sec=60)
    _, t = _run(w, lambda i: bg(), lambda i: [], 8, 1000.0)
    # un box de tracker acopera mereu regiunea -> nu e "obiect lasat", ci ceva in miscare
    ev, _ = _run(w, lambda i: with_obj(), lambda i: [[390, 290, 445, 345]], 30, t)
    assert not ev


def test_lumina_globala_ignorata():
    w = ai_scene.SceneWatcher(14, persist_sec=60)
    _, t = _run(w, lambda i: bg(120), lambda i: [], 8, 1000.0)
    ev, _ = _run(w, lambda i: bg(195), lambda i: [], 30, t)      # tot cadrul mai luminos
    assert not ev


def test_throttle_pe_sample_sec():
    w = ai_scene.SceneWatcher(14, persist_sec=60)
    # doua feed-uri la <SAMPLE_SEC distanta: al doilea e ignorat (nu ia mostra)
    assert w.feed(bg(), [], 1000.0) == []
    assert len(w.samples) == 1
    assert w.feed(bg(), [], 1000.0 + S / 2) == []
    assert len(w.samples) == 1                                   # nu a adaugat inca
