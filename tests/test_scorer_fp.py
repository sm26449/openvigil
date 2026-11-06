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
    scorer_fp.reset()        # goleste cache-ul de numere + circuit breaker (izolare intre teste)
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


# --- B2: vetoul semantic (dovada „real" blocheaza suprimarea; invarianta: doar scoate suprimare) ---

def test_veto_blocheaza_suprimarea_pentru_crop_nou():
    """Celula invatata ca fals (pozitional ar suprima), dar embedding-ul NU seamana cu niciun
    fals invatat -> obiect nou/real -> veto -> NU suprimam."""
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit", emb=V(0))
    assert scorer_fp.scor(6, "person", "noapte", HOT)["suprima"] is True       # pozitional (fara emb)
    s = scorer_fp.scor(6, "person", "noapte", HOT, emb=V(5))                   # crop diferit de falsuri
    assert s["veto"] is True and s["suprima"] is False


def test_fara_veto_cand_cropul_seamana_cu_falsul():
    """Acelasi crop ca falsurile invatate -> fara veto -> suprimarea pozitionala ramane."""
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit", emb=V(0))
    s = scorer_fp.scor(6, "person", "noapte", HOT, emb=V(0))
    assert s["veto"] is False and s["suprima"] is True


def test_veto_cand_seamana_mai_mult_cu_un_exemplar_real():
    """Un exemplar „ok" (real) apropiat de crop (chiar in alta celula) blocheaza suprimarea."""
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit", emb=V(0))
    scorer_fp.adauga(6, "person", "zi", ALT, "ok", emb=V(5))   # real, alt patratel -> ok ramane 0 in HOT
    s = scorer_fp.scor(6, "person", "noapte", HOT, emb=V(5))
    assert s["veto"] is True and s["suprima"] is False


# --- B3: cache pe celule + invalidare la adauga + circuit breaker fail-open ---

def test_cache_serveste_si_adaugarea_il_invalideaza():
    for _ in range(3):
        scorer_fp.adauga(6, "person", "noapte", HOT, "gresit")
    assert scorer_fp.scor(6, "person", "noapte", HOT)["fals"] == 3      # populeaza cache-ul
    with db.conn() as c:
        c.execute("DELETE FROM vectori_fp WHERE cam=6")                # ocolim adauga
    assert scorer_fp.scor(6, "person", "noapte", HOT)["fals"] == 3      # inca din cache
    scorer_fp.adauga(6, "person", "noapte", HOT, "ok")                 # adauga invalideaza celula
    assert scorer_fp.scor(6, "person", "noapte", HOT)["ok"] == 1       # numere proaspete


def test_circuit_breaker_fail_open(monkeypatch):
    """PG picat la numarare -> (0,0) fail-open (nu suprima nimic), iar dupa CB_PRAG erori
    breaker-ul se deschide (ocolim PG un timp)."""
    def crapa(*a, **k):
        raise RuntimeError("PG jos")
    monkeypatch.setattr(scorer_fp, "_numara", crapa)
    for _ in range(scorer_fp.CB_PRAG):
        assert scorer_fp.scor(6, "person", "noapte", HOT)["suprima"] is False
    assert scorer_fp.stare()["cb_deschis"] is True


# --- B4/B5: dedup dupa id (ultimul verdict), reincarcare atomica ---

def _scrie(tmp_path, *entries):
    import json
    p = tmp_path / "feedback.jsonl"
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    return str(p)


def test_dedup_dupa_id_nu_umfla_numaratoarea(tmp_path):
    """Acelasi verdict repetat pe aceeasi detectie (acelasi id) se numara O SINGURA DATA."""
    comun = {"cam": 6, "verdict": "gresit", "eticheta_initiala": "person", "regiune": HOT}
    p = _scrie(tmp_path,
               {**comun, "id": "d1", "t": "2026-11-04 02:00:00"},
               {**comun, "id": "d1", "t": "2026-11-04 02:01:00"},   # duplicat -> ignorat
               {**comun, "id": "d2", "t": "2026-11-04 02:02:00"},
               {**comun, "id": "d3", "t": "2026-11-04 02:03:00"})
    assert scorer_fp.incarca_din_feedback(p) == 3                   # nu 4
    assert scorer_fp.scor(6, "person", "noapte", HOT)["fals"] == 3


def test_ultimul_verdict_scoate_detectia(tmp_path):
    """Daca omul se razgandeste (gresit -> ok pe acelasi id), ultimul verdict conteaza."""
    comun = {"cam": 6, "eticheta_initiala": "person", "regiune": HOT}
    p = _scrie(tmp_path,
               {**comun, "id": "d1", "verdict": "gresit", "t": "2026-11-04 02:00:00"},
               {**comun, "id": "d1", "verdict": "ok", "t": "2026-11-04 02:05:00"})
    scorer_fp.incarca_din_feedback(p)
    s = scorer_fp.scor(6, "person", "noapte", HOT)
    assert s["fals"] == 0 and s["ok"] == 1


def test_reincarcare_atomica_rollback(tmp_path):
    """Un embedding invalid face INSERT-ul sa crape IN tranzactie -> rollback; memoria veche ramane."""
    comun = {"cam": 6, "verdict": "gresit", "eticheta_initiala": "person", "regiune": HOT}
    p = _scrie(tmp_path,
               {**comun, "id": "d1", "t": "2026-11-04 02:00:00"},
               {**comun, "id": "d2", "t": "2026-11-04 02:01:00"},
               {**comun, "id": "d3", "t": "2026-11-04 02:02:00"})
    assert scorer_fp.incarca_din_feedback(p) == 3
    assert scorer_fp.scor(6, "person", "noapte", HOT)["fals"] == 3

    def embed_dim_gresit(_poza):
        return [0.1, 0.2, 0.3]            # 3-dim in loc de 768 -> ::vector esueaza la INSERT
    with pytest.raises(Exception):
        scorer_fp.incarca_din_feedback(p, embed_fn=embed_dim_gresit)
    assert scorer_fp.scor(6, "person", "noapte", HOT)["fals"] == 3   # neschimbat (rollback)


def test_embeddings_pastrate_la_resync_pozitional(tmp_path):
    """Backfill cu embed_fn scrie embeddings; re-sync-ul pozitional (fara embed_fn, ca
    `_scorer_sync` dupa un verdict) le PASTREAZA (match pe (cam, poza)), deci vetoul semantic B2
    ramane activ — nu revine pe tacute la pur pozitional."""
    comun = {"cam": 6, "verdict": "gresit", "eticheta_initiala": "person", "regiune": HOT}
    p = _scrie(tmp_path,
               {**comun, "id": "d1", "t": "2026-11-04 02:00:00", "poza": "a.jpg"},
               {**comun, "id": "d2", "t": "2026-11-04 02:01:00", "poza": "b.jpg"},
               {**comun, "id": "d3", "t": "2026-11-04 02:02:00", "poza": "c.jpg"})
    scorer_fp.incarca_din_feedback(p, embed_fn=lambda _poza: V(0))        # backfill: falsuri ~ V(0)
    assert scorer_fp.scor(6, "person", "noapte", HOT, emb=V(5))["veto"] is True   # crop nou -> veto

    scorer_fp.incarca_din_feedback(p)                                    # re-sync POZITIONAL (fara embed_fn)
    s = scorer_fp.scor(6, "person", "noapte", HOT, emb=V(5))
    assert s["fals"] == 3 and s["veto"] is True and s["suprima"] is False  # embeddings au supravietuit

    # o detectie noua, fara embedding in baza, ramane pur pozitionala (nimic de pastrat)
    q = _scrie(tmp_path,
               {**comun, "id": "d1", "t": "2026-11-04 02:00:00", "poza": "a.jpg"},
               {**comun, "id": "d4", "t": "2026-11-04 02:03:00", "poza": "nou.jpg"})
    scorer_fp.incarca_din_feedback(q)                                    # d2,d3 ies; d4 nou, fara emb
    with db.conn() as c:
        fara = c.execute("SELECT count(*) FROM vectori_fp WHERE cam=6 AND emb IS NULL").fetchone()[0]
        cu = c.execute("SELECT count(*) FROM vectori_fp WHERE cam=6 AND emb IS NOT NULL").fetchone()[0]
    assert cu == 1 and fara == 1          # 'a.jpg' si-a pastrat emb, 'nou.jpg' n-are
