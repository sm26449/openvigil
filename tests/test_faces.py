"""Teste pentru app/faces.py — biblioteca de persoane si potrivirea embeddings.

Nu ating modelul ONNX (lent, descarcat separat): testeaza logica pura — igiena
numelor, potrivirea cosinus cu praguri, caile sigure, cache-ul de embeddings.
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import faces  # noqa: E402


@pytest.fixture(autouse=True)
def _faces_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(faces, "FACES_DIR", str(tmp_path))
    monkeypatch.setattr(faces, "PERSONS_DIR", str(tmp_path / "persons"))
    monkeypatch.setattr(faces, "UNKNOWN_DIR", str(tmp_path / "unknown"))
    monkeypatch.setattr(faces, "EMB_FILE", str(tmp_path / "embeddings.json"))
    faces._emb_cache.update(mtime=None, data={})
    faces._unknown_last.clear()


def _unit(v):
    v = np.array(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_sanitize_nume_cu_diacritice():
    assert faces.sanitize_name("Ștefan") == "stefan"
    assert faces.sanitize_name("Ana Maria") == "ana-maria"
    assert faces.sanitize_name("ion.2") == "ion-2"


def test_sanitize_nume_invalid():
    for rau in ("", "a", "1234", "x" * 30, "!!!"):
        with pytest.raises(ValueError):
            faces.sanitize_name(rau)
    # liniuta din fata/spate se curata singura, nu e motiv de refuz
    assert faces.sanitize_name("-inceput-") == "inceput"


def test_match_biblioteca_goala():
    name, sim = faces.match(_unit([1, 0, 0]), threshold=0.45)
    assert name is None


def test_match_peste_si_sub_prag():
    faces._save_embeddings({"ion": np.stack([_unit([1, 0, 0])]),
                            "ana": np.stack([_unit([0, 1, 0])])})
    # identic cu ion -> similaritate 1.0
    name, sim = faces.match(_unit([1, 0, 0]), threshold=0.45)
    assert name == "ion" and sim > 0.99
    # intre ion si ana, dar mai aproape de ana
    name, sim = faces.match(_unit([0.2, 1, 0]), threshold=0.45)
    assert name == "ana"
    # departe de amandoi -> necunoscut, dar cu cea mai buna similaritate raportata
    name, sim = faces.match(_unit([0, 0, 1]), threshold=0.45)
    assert name is None and sim < 0.1


def test_match_ia_maximul_dintre_pozele_persoanei():
    faces._save_embeddings({"ion": np.stack([_unit([1, 0, 0]), _unit([0, 1, 0])])})
    name, sim = faces.match(_unit([0, 1, 0]), threshold=0.45)
    assert name == "ion" and sim > 0.99


def test_cache_embeddings_se_reincarca_la_schimbare():
    faces._save_embeddings({"ion": np.stack([_unit([1, 0, 0])])})
    assert faces.match(_unit([1, 0, 0]), threshold=0.45)[0] == "ion"
    faces._save_embeddings({"ana": np.stack([_unit([1, 0, 0])])})
    assert faces.match(_unit([1, 0, 0]), threshold=0.45)[0] == "ana"


def test_safe_path_blocheaza_traversarea():
    assert faces.safe_path("../../etc/passwd") is None
    assert faces.safe_path("unknown/../../../etc/passwd") is None
    ok = faces.safe_path("unknown/2026-08-13/120000_cam101.jpg")
    assert ok and ok.startswith(faces.FACES_DIR)


def test_unknown_list_goala_si_stergere_totala():
    assert faces.unknown_list() == []
    faces.delete_unknown()   # nu exista directorul -> nu crapa


def test_persons_gol():
    assert faces.persons() == []


def test_delete_person_inexistenta():
    with pytest.raises(ValueError):
        faces.delete_person("nimeni")


def _persoana_pe_disc(nume, emb):
    d = os.path.join(faces.PERSONS_DIR, nume)
    os.makedirs(d, exist_ok=True)
    open(os.path.join(d, "1.jpg"), "wb").close()
    data = faces._load_embeddings().copy()
    data[nume] = np.stack([_unit(emb)])
    faces._save_embeddings(data)


def test_rename_person_muta_directorul_si_embeddings():
    _persoana_pe_disc("rezident-1", [1, 0, 0])
    assert faces.rename_person("Rezident 1", "Ștefan") == "stefan"
    assert os.path.isdir(os.path.join(faces.PERSONS_DIR, "stefan"))
    assert not os.path.exists(os.path.join(faces.PERSONS_DIR, "rezident-1"))
    lib = faces._load_embeddings()
    assert "stefan" in lib and "rezident-1" not in lib
    # potrivirea merge pe numele nou
    assert faces.match(_unit([1, 0, 0]), threshold=0.45)[0] == "stefan"


def test_rename_person_refuza_conflict_si_inexistent():
    _persoana_pe_disc("ion", [1, 0, 0])
    _persoana_pe_disc("ana", [0, 1, 0])
    with pytest.raises(ValueError):
        faces.rename_person("ion", "ana")          # numele nou exista deja
    with pytest.raises(ValueError):
        faces.rename_person("nimeni", "altcineva")  # persoana nu exista
    with pytest.raises(ValueError):
        faces.rename_person("ion", "!!!")           # nume nou invalid
    # nimic nu s-a stricat
    assert os.path.isdir(os.path.join(faces.PERSONS_DIR, "ion"))


def test_combine_spoof_respinge_doar_cand_ambele_zic_fals():
    # ambele false, incredere mare -> respins
    real, conf = faces.combine_spoof([(False, 0.9), (False, 0.8)], 0.7)
    assert not real and conf == pytest.approx(0.85)
    # doar unul zice fals -> trece (o fata reala vazuta prost nu pierde recunoasterea)
    real, _ = faces.combine_spoof([(False, 0.9), (True, 0.8)], 0.7)
    assert real
    # ambele false dar incredere sub prag -> trece
    real, _ = faces.combine_spoof([(False, 0.6), (False, 0.5)], 0.7)
    assert real
    # ambele reale -> trece
    real, _ = faces.combine_spoof([(True, 0.9), (True, 0.9)], 0.7)
    assert real
    # fara rezultate -> trece (esecul verificarii nu blocheaza recunoasterea)
    real, conf = faces.combine_spoof([], 0.7)
    assert real and conf == 0.0



def test_emit_ok_racord_pe_minut():
    faces._seen_emit.clear()
    assert faces.emit_ok(7, "stefan", now=100.0)          # prima: da
    assert not faces.emit_ok(7, "stefan", now=130.0)      # sub minut: nu
    assert faces.emit_ok(7, "stefan", now=161.0)          # dupa minut: da
    assert faces.emit_ok(8, "stefan", now=131.0)          # alta camera: separat


def test_face_retry_due():
    import detect
    # fara om in cadru -> niciodata
    assert not detect.face_retry_due(False, 100, 0, 0)
    # om prezent, prima incercare -> da
    assert detect.face_retry_due(True, 100, 0, 0)
    # sub intervalul de reincercare -> nu
    assert not detect.face_retry_due(True, 103, 100, 0)
    # peste interval, inca nerecunoscut -> da (te-ai intors spre camera)
    assert detect.face_retry_due(True, 106, 100, 0)
    # recunoscut recent -> badge-ul e viu, nu reincercam
    assert not detect.face_retry_due(True, 106, 100, 104)
    # recunoasterea s-a invechit (>10s) -> reimprospatam
    assert detect.face_retry_due(True, 115, 106, 104)
