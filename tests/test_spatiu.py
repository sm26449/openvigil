"""Spatiul pe camera nu trebuie sa pice cand retentia sterge un segment intre listare si
masurare (cursa reala: /api/retention si /api/storage intorceau 500 in minutul curatarii)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import record  # noqa: E402
import routes_system as rs  # noqa: E402


def test_segment_sters_intre_listare_si_masurare(tmp_path, monkeypatch):
    cam = tmp_path / "cam7"
    cam.mkdir()
    real = cam / "2026-11-07_16-50-02Z.mp4"
    real.write_bytes(b"x" * 1000)
    disparut = cam / "2026-11-07_16-55-02Z.mp4"          # listat, apoi sters de retentie
    monkeypatch.setattr(record, "REC_DIR", str(tmp_path))
    monkeypatch.setattr(record, "_files", lambda cid: [(1.0, str(real), real.name), (2.0, str(disparut), disparut.name)])
    rs._spatiu_memo.update(t=0, v={})
    out = rs.spatiu_per_camera(ttl=0)
    assert out[7]["octeti"] == 1000
    assert out[7]["segmente"] == 2
