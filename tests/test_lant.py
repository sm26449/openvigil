"""Lantul de custodie (app/lant.py): hash la inchidere, tur, verificare, mutare, curatare."""
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import db  # noqa: E402
import lant  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare():
    # din Faza 3 lantul e pe PostgreSQL (baza partajata): sare daca PG lipseste, altfel
    # goleste tabelul `segmente` inainte de fiecare test
    try:
        db.ping()
    except Exception as e:
        pytest.skip(f"PostgreSQL indisponibil: {e}")
    lant._init()
    with db.conn() as c:
        c.execute("TRUNCATE segmente")
    yield


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


def test_recodat_pastreaza_amprenta_originalului(tmp_path):
    """Dupa re-encodare: lantul retine hash-ul nou (verifica() ramane valabila) dar pastreaza
    amprenta originalului in sha256_orig si momentul recodarii."""
    now = 1_800_000_000
    a = _seg(tmp_path, 9, now - 1000, continut=b"video-brut")
    lant.inregistreaza(9, a[0], a[1])
    sha_orig = lant.get(9, a[2])["sha256"]
    arh = tmp_path / "arhiva"; arh.mkdir()
    nou = str(arh / a[2])
    with open(nou, "wb") as f:
        f.write(b"hevc")
    lant.recodat(9, a[2], nou)
    r = lant.get(9, a[2])
    assert r["cale"] == nou and r["recodat"] and r["arhivat"]
    assert r["sha256"] == lant.hash_fisier(nou) and r["octeti"] == 4 and r["sha256_orig"] == sha_orig
    v = lant.verifica(9, a[2])
    assert v["ok"] is True and v["sha256_orig"] == sha_orig and v["recodat"]
    # a doua recodare NU pierde amprenta originalului initial
    nou2 = str(arh / "x2.mp4")
    with open(nou2, "wb") as f:
        f.write(b"av1")
    lant.recodat(9, a[2], nou2)
    assert lant.get(9, a[2])["sha256_orig"] == sha_orig
    # segment fara amprenta -> recodat nu face nimic
    assert lant.recodat(9, "inexistent.mp4", nou2) is None


def test_migrarea_adauga_coloanele_de_recodare():
    """O baza veche (segmente la versiunea 1, fara sha256_orig/recodat) primeste coloanele prin
    migrarea 2 — calea prin care se face upgrade-ul in productie."""
    with db.conn() as c:
        c.execute("DROP TABLE IF EXISTS segmente")
        c.execute("CREATE TABLE segmente (cam INTEGER NOT NULL, nume TEXT NOT NULL, "
                  "epoch BIGINT NOT NULL, cale TEXT NOT NULL, octeti BIGINT NOT NULL, "
                  "sha256 TEXT NOT NULL, calculat DOUBLE PRECISION NOT NULL, "
                  "arhivat DOUBLE PRECISION, PRIMARY KEY(cam, nume))")
        c.execute("INSERT INTO schema_versiuni(nume, versiune) VALUES('lant', 1) "
                  "ON CONFLICT(nume) DO UPDATE SET versiune = 1")
    lant.reset()
    db.aplica("lant", lant.SCHEMA, lant.MIGRARI)
    with db.conn() as c:
        cols = {r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='segmente'"
        ).fetchall()}
    assert "sha256_orig" in cols and "recodat" in cols


def test_caile_absolute_vechi_se_rezolva_in_radacina_noua(tmp_path, monkeypatch):
    """Lantul scris de o instalare veche (cai absolute de pe gazda) ramane valabil cand codul
    ruleaza din alt director sau din container: se recalculeaza fata de ROOT."""
    rec = tmp_path / "radacina" / "data" / "recordings" / "cam1"
    rec.mkdir(parents=True)
    f = rec / "2026-10-25_18-45-08Z.mp4"
    f.write_bytes(b"x" * 100)
    monkeypatch.setattr(lant, "ROOT", str(tmp_path / "radacina"))
    with db.conn() as c:
        c.execute("INSERT INTO segmente(cam, nume, epoch, cale, octeti, sha256, calculat) "
                  "VALUES(1, %s, 1, %s, 100, %s, 1)",
                  (f.name, "/alta/gazda/data/recordings/cam1/" + f.name, lant.hash_fisier(str(f))))
    assert lant.get(1, f.name)["cale"] == str(f)
    assert lant.verifica(1, f.name)["ok"] is True
    # ce se scrie de acum e relativ la radacina
    lant.inregistreaza(1, 2, str(f))
    with db.conn() as c:
        rand = c.execute("SELECT cale FROM segmente WHERE cam=1 AND nume=%s", (f.name,)).fetchone()
    assert rand[0] == "data/recordings/cam1/" + f.name
