"""Lantul de custodie (app/lant.py): hash la inchidere, tur, verificare, mutare, curatare."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import lant  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    lant.reset()
    monkeypatch.setattr(lant, "DB_FILE", str(tmp_path / "segmente.db"))
    yield
    lant.reset()


def _seg(tmp_path, cam, epoch, continut=b"video"):
    d = tmp_path / f"cam{cam}"
    d.mkdir(exist_ok=True)
    nume = time.strftime("%Y-%m-%d_%H-%M-%SZ.mp4", time.gmtime(epoch))
    p = d / nume
    p.write_bytes(continut)
    return (epoch, str(p), nume)


def test_tur_hash_uieste_doar_segmentele_inchise(tmp_path):
    now = 1_800_000_000
    a = _seg(tmp_path, 5, now - 1000)            # inchis demult
    b = _seg(tmp_path, 5, now - 200)             # inca se scrie (300 + 60 margine)
    files = {5: [a, b]}
    n = lant.tur(lambda cam: files[cam], [5], segment=300, now=now)
    assert n == 1
    r = lant.get(5, a[2])
    assert r and r["octeti"] == 5 and r["sha256"] == lant.hash_fisier(a[1]) and r["arhivat"] is None
    assert lant.get(5, b[2]) is None
    assert lant.tur(lambda cam: files[cam], [5], segment=300, now=now) == 0      # idempotent
    assert lant.tur(lambda cam: files[cam], [5], segment=300, now=now + 400) == 1  # b s-a inchis
    vechi = _seg(tmp_path, 5, now - 5 * 86400)   # restanta mai veche de 2 zile: nu se mai hash-uieste
    assert lant.tur(lambda cam: files[cam] + [vechi], [5], segment=300, now=now + 400) == 0
    assert lant.tur(lambda cam: [vechi], [5], segment=300, now=now + 400, inapoi=0) == 1
    assert lant.lista(5, since=now - 1500, until=now)[0]["nume"] == a[2]
    assert [x["nume"] for x in lant.lista(5)] == [vechi[2], a[2], b[2]]   # ordonat dupa epoch


def test_verificare_mutare_si_curatare(tmp_path):
    now = 1_800_000_000
    a = _seg(tmp_path, 7, now - 1000)
    lant.inregistreaza(7, a[0], a[1])
    assert lant.verifica(7, a[2])["ok"] is True
    with open(a[1], "ab") as f:
        f.write(b"!")
    v = lant.verifica(7, a[2])
    assert v["ok"] is False and "MODIFICAT" in v["motiv"]
    assert lant.verifica(7, "nu-exista.mp4")["ok"] is None
    # mutare in arhiva: calea noua, marcajul de arhivare
    nou = str(tmp_path / "arhiva.mp4")
    os.replace(a[1], nou)
    lant.muta(7, a[2], nou)
    r = lant.get(7, a[2])
    assert r["cale"] == nou and r["arhivat"]
    assert lant.verifica(7, a[2])["ok"] is False           # continutul modificat mai sus
    # curatare: fisierul nu mai e listat -> amprenta dispare
    assert lant.curata(lambda cam: [], [7]) == 1 and lant.get(7, a[2]) is None


def test_caile_absolute_vechi_se_rezolva_in_radacina_noua(tmp_path, monkeypatch):
    """Lantul scris de o instalare veche (cai absolute de pe gazda) ramane valabil cand codul
    ruleaza din alt director sau din container: se recalculeaza fata de ROOT."""
    rec = tmp_path / "radacina" / "data" / "recordings" / "cam1"
    rec.mkdir(parents=True)
    f = rec / "2026-10-25_18-45-08Z.mp4"
    f.write_bytes(b"x" * 100)
    monkeypatch.setattr(lant, "ROOT", str(tmp_path / "radacina"))
    lant._c().execute("INSERT INTO segmente(cam, nume, epoch, cale, octeti, sha256, calculat) "
                      "VALUES(1, ?, 1, ?, 100, ?, 1)",
                      (f.name, "/alta/gazda/data/recordings/cam1/" + f.name, lant.hash_fisier(str(f))))
    assert lant.get(1, f.name)["cale"] == str(f)
    assert lant.verifica(1, f.name)["ok"] is True
    # ce se scrie de acum e relativ la radacina
    lant.inregistreaza(1, 2, str(f))
    rand = lant._c().execute("SELECT cale FROM segmente WHERE cam=1 AND nume=?", (f.name,)).fetchone()
    assert rand[0] == "data/recordings/cam1/" + f.name
