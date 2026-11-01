"""Pozele de detectie: sidecar-ul cu chenare (<poza>.json) se scrie langa poza (importul
`store` lipsea, iar sidecarul nu se scria niciodata)."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import snaps  # noqa: E402


def test_sidecar_cu_chenare(tmp_path, monkeypatch):
    np = pytest.importorskip("numpy")
    pytest.importorskip("cv2")
    monkeypatch.setattr(snaps, "_cooldown_ok", lambda cam, label: True)
    scrise = {}
    monkeypatch.setattr(snaps, "_save_bytes", lambda cam, label, b: str(tmp_path / "p.jpg"))
    monkeypatch.setattr(snaps.store, "atomic_write_json",
                        lambda path, data, mode=0o600, **k: scrise.update({path: data}))
    snaps.save_frame(14, "person", np.zeros((8, 8, 3), np.uint8), boxes=[{"cls": "person", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.3, "conf": 0.9}])
    assert scrise == {str(tmp_path / "p.jpg") + ".json": {"boxes": [{"cls": "person", "x": 0.1, "y": 0.1, "w": 0.2, "h": 0.3, "conf": 0.9}]}}
