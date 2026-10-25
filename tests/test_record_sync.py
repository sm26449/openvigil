"""record.sync: recorderul reporneste cand se schimba conexiunea camerei (ip/parola/vendor),
nu doar cand se schimba lista de id-uri."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import record  # noqa: E402


class Fals:
    pornite = []
    def __init__(self, cam):
        self.cam = cam; self.stop_flag = False; Fals.pornite.append(self)
    def start(self): pass
    def is_alive(self): return not self.stop_flag


def test_sync_reporneste_la_schimbarea_conexiunii(monkeypatch):
    monkeypatch.setattr(record, "Recorder", Fals)
    record.recorders.clear(); Fals.pornite.clear()
    a = {"id": 5, "ip": "10.0.0.5", "user": "admin", "pass": "x"}
    record.sync([a])
    assert len(Fals.pornite) == 1 and record.recorders[5].sig == record.semnatura(a)
    record.sync([dict(a)])                                   # neschimbat: nimic
    assert len(Fals.pornite) == 1
    record.sync([{**a, "pass": "y"}])                        # parola noua: recorder nou
    assert len(Fals.pornite) == 2 and Fals.pornite[0].stop_flag and not Fals.pornite[1].stop_flag
    record.sync([{**a, "pass": "y", "name": "alt nume", "group": "G"}])   # campuri fara legatura: nimic
    assert len(Fals.pornite) == 2
    record.sync([])
    assert Fals.pornite[1].stop_flag and not record.recorders
