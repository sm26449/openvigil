"""Scorerul de falsuri (app/scorer_fp.py): memorie pozitionala + semantica, pe PostgreSQL+pgvector.

Vectorii sunt sintetici (nu cheama SigLIP). Testele se sar daca PostgreSQL nu e disponibil.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db  # noqa: E402
import scorer_fp  # noqa: E402

HOT = [0.76, 0.56, 0.02, 0.06]     # fasia IR cam 6: centru ~ (0.77, 0.59) -> patratelul (14, 24)
ALT = [0.10, 0.10, 0.02, 0.02]     # alt colt al cadrului


def V(i):
    """Vector 768-dim cu 1.0 pe pozitia i (cosinus = cat de aproape e indexul)."""
    v = [0.0] * scorer_fp.DIM
    v[i] = 1.0
    return v


@pytest.fixture(autouse=True)
def _pg():
    try:
        db.ping()
    except Exception as e:
        pytest.skip(f"PostgreSQL indisponibil: {e}")
    scorer_fp._init()
    with db.conn() as c:
        c.execute("TRUNCATE vectori_fp")
    yield


def test_celula_si_regim():
    assert scorer_fp.celula(HOT) == (int(0.59 * 24), int(0.77 * 32))
    assert scorer_fp.regim_din_ora(2) == "noapte" and scorer_fp.regim_din_ora(23) == "noapte"
    assert scorer_fp.regim_din_ora(12) == "zi"


def test_suprimare_pozitionala_si_plasa_ok():
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit")
    s = scorer_fp.scor(6, "person", "noapte", HOT)
    assert s["fals"] == 3 and s["ok"] == 0 and s["suprima"] is True
    # un singur „ok” in acelasi patratel ridica mutul (plasa de siguranta)
    scorer_fp.adauga(6, "person", "noapte", HOT, "ok")
    assert scorer_fp.suprima(6, "person", "noapte", HOT) is False


def test_prag_minim_de_falsuri():
    for _ in range(2):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit")
    assert scorer_fp.suprima(6, "person", "noapte", HOT) is False   # doar 2 < MIN_FALS


def test_alt_patratel_si_alt_regim_nu_sunt_atinse():
    for _ in range(5):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit")
    assert scorer_fp.suprima(6, "person", "noapte", ALT) is False        # alt patratel
    assert scorer_fp.suprima(6, "person", "zi", HOT) is False            # alt regim
    assert scorer_fp.suprima(6, "car", "noapte", HOT) is False           # alta eticheta


def test_termenul_semantic_creste_cu_asemanarea():
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit", emb=V(0))
    la_fals = scorer_fp.scor(6, "person", "noapte", HOT, emb=V(0))
    departe = scorer_fp.scor(6, "person", "noapte", HOT, emb=V(5))
    assert la_fals["emb_term"] > departe["emb_term"]
    assert la_fals["emb_term"] == pytest.approx(1.0, abs=1e-3)


def test_semantic_scade_cand_seamana_cu_un_real():
    # fals ~ V(0), dar un „ok” ~ V(0) la fel: termenul semantic se anuleaza
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit", emb=V(0))
    scorer_fp.adauga(6, "person", "zi", ALT, "ok", emb=V(0))   # alt patratel, dar exemplar real apropiat
    s = scorer_fp.scor(6, "person", "noapte", HOT, emb=V(0))
    assert s["emb_term"] == pytest.approx(0.0, abs=1e-3)   # sim_fals - sim_ok ~ 0


def test_incarca_din_feedback(tmp_path):
    import json
    linii = []
    for i in range(5):
        linii.append(json.dumps({"cam": 6, "verdict": "gresit", "eticheta_initiala": "person",
                                 "regiune": HOT, "t": "2026-11-04 02:1%d:00" % i,
                                 "poza": f"p{i}.jpg"}))
    linii.append(json.dumps({"cam": 6, "verdict": "ok", "eticheta_initiala": "person",
                             "regiune": ALT, "t": "2026-11-04 13:00:00"}))
    linii.append("{stricat")                                   # linie stricata -> sarita
    p = tmp_path / "feedback.jsonl"
    p.write_text("\n".join(linii) + "\n")

    n = scorer_fp.incarca_din_feedback(str(p))
    assert n == 6                                              # 5 gresit + 1 ok (stricata sarita)
    assert scorer_fp.suprima(6, "person", "noapte", HOT) is True
    # a doua incarcare nu dubleaza (goleste camera inainte)
    scorer_fp.incarca_din_feedback(str(p))
    s = scorer_fp.scor(6, "person", "noapte", HOT)
    assert s["fals"] == 5
