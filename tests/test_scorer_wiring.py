"""Cablarea scorerului de falsuri in bucla de detectie (detect.CamState._mut_invatat, Faza 4b).

Testeaza gate-ul (box -> patratel -> suprimare), opt-in per camera si fail-safe-ul, fara sa
cheme modelul YOLO. Se sare daca PostgreSQL nu e disponibil.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db  # noqa: E402
import detect  # noqa: E402
import scorer_fp  # noqa: E402

HOT = {"lbl": "person", "x": 0.76, "y": 0.56, "w": 0.02, "h": 0.06}
HOT_BOX = [0.76, 0.56, 0.02, 0.06]


@pytest.fixture(autouse=True)
def _pg():
    try:
        db.ping()
    except Exception as e:
        pytest.skip(f"PostgreSQL indisponibil: {e}")
    scorer_fp.reset()        # goleste cache-ul de numere + circuit breaker (izolare intre teste)
    scorer_fp._init()
    with db.conn() as c:
        c.execute("TRUNCATE vectori_fp")
    yield


def test_gate_suprima_doar_dupa_ce_a_invatat():
    cs = detect.CamState(6)
    assert cs._mut_invatat(HOT, noapte=True) is False          # memoria goala -> nu suprima
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT_BOX, "gresit")
    assert cs._mut_invatat(HOT, noapte=True) is True           # invatat ca fals
    assert cs._mut_invatat(HOT, noapte=False) is False         # ziua (alt regim) ramane atins
    assert cs._mut_invatat({**HOT, "lbl": "car"}, noapte=True) is False   # alta eticheta


def test_gate_e_fail_safe(monkeypatch):
    """Daca scorerul arunca (ex. PostgreSQL pica), gate-ul intoarce False -> alarma tot suna."""
    cs = detect.CamState(6)

    def crapa(*a, **k):
        raise RuntimeError("PG picat")
    monkeypatch.setattr(scorer_fp, "scor", crapa)   # _mut_invatat cheama scor() (pozitional + veto)
    assert cs._mut_invatat(HOT, noapte=True) is False


# --- B1: gate-ul comun (acelasi filtru pe substream SI pe tiling/`regiune`) ---

def _ctx(**over):
    baza = {"zone": None, "invata": False, "semantic": False, "noapte": False,
            "e_noapte": False, "prag_noapte": 0.0, "prag_vreme": 0.0}
    baza.update(over)
    return baza


def test_gate_comun_aplica_suprimarea_invatata():
    """Fix B1: suprimarea invatata trebuie sa prinda si calea de tiling (prin _gate_comun),
    nu doar substream-ul."""
    cs = detect.CamState(6)
    box = {**HOT, "conf": 0.9}
    ctx = _ctx(invata=True, noapte=True)
    assert cs._gate_comun(box, ctx) is None                 # memoria goala -> trece
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT_BOX, "gresit")
    assert cs._gate_comun(box, ctx) == "invatat"            # invatat ca fals -> respins pe AMBELE cai


def test_gate_comun_zona_si_praguri():
    cs = detect.CamState(6)
    poly = [[0.025, 0.025], [0.2, 0.025], [0.2, 0.2], [0.025, 0.2]]
    ctx = _ctx(zone=poly, e_noapte=True, prag_noapte=0.5)
    inauntru = {"lbl": "person", "x": 0.03, "y": 0.03, "w": 0.02, "h": 0.02, "conf": 0.9}
    afara = {"lbl": "person", "x": 0.5, "y": 0.5, "w": 0.02, "h": 0.02, "conf": 0.9}
    slab = {"lbl": "person", "x": 0.03, "y": 0.03, "w": 0.02, "h": 0.02, "conf": 0.3}
    assert cs._gate_comun(inauntru, ctx) is None            # in zona, conf peste pragul de noapte
    assert cs._gate_comun(afara, ctx) == "zona"             # in afara zonei
    assert cs._gate_comun(slab, ctx) == "noapte"            # conf sub pragul de noapte
