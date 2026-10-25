"""verificare: registrul exporturilor, hash pe flux cu limita, cautarea dupa amprenta (export, segment, necunoscut)."""
import hashlib
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import verificare  # noqa: E402


def test_hash_flux_si_limita():
    data = b"x" * 3000
    sha, n = verificare.hash_flux(io.BytesIO(data))
    assert sha == hashlib.sha256(data).hexdigest() and n == 3000
    assert verificare.hash_flux(io.BytesIO(data), limita=100) == (None, 1048576 if False else 3000) or verificare.hash_flux(io.BytesIO(data), limita=100)[0] is None


def test_registru_si_cautare(tmp_path, monkeypatch):
    monkeypatch.setattr(verificare, "FISIER", str(tmp_path / "exporturi.jsonl"))
    sha = "a" * 64
    verificare.inregistreaza(sha, 1234, 13, "20261024T100000Z", "20261024T100030Z", cine="admin", nume="cam13_x.mp4")
    r = verificare.cauta(sha, {13: "Strada"})
    assert r["gasit"] and r["fel"] == "export" and r["cam_nume"] == "Strada" and r["cine"] == "admin"
    assert verificare.cauta("b" * 64)["gasit"] is False
    assert verificare.cauta("nu-e-hash")["gasit"] is False
    # amprenta unui segment brut vine din lantul de custodie
    import lant
    monkeypatch.setattr(lant, "cauta_hash", lambda s: {"cam": 7, "nume": "2026-10-24_10-00-00Z.mp4", "calculat": 1792800000, "octeti": 99} if s == "c" * 64 else None)
    s = verificare.cauta("c" * 64, {7: "Curte"})
    assert s["gasit"] and s["fel"] == "segment" and s["cam_nume"] == "Curte" and s["nume"].endswith("Z.mp4")
