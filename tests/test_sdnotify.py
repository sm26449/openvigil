"""sd_notify fara biblioteci: trimite pe NOTIFY_SOCKET, no-op fara el."""
import os
import socket
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import sdnotify  # noqa: E402
import store  # noqa: E402


def test_notify(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    assert sdnotify.ready() is False
    cale = str(tmp_path / "notify.sock")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    srv.bind(cale); srv.settimeout(2)
    monkeypatch.setenv("NOTIFY_SOCKET", cale)
    assert sdnotify.ready() and srv.recv(64) == b"READY=1"
    assert sdnotify.watchdog() and srv.recv(64) == b"WATCHDOG=1"
    srv.close()


def test_atomic_write_text(tmp_path):
    p = tmp_path / "a.yaml"
    store.atomic_write_text(str(p), "x: 1\n", mode=0o600)
    assert p.read_text() == "x: 1\n" and oct(p.stat().st_mode)[-3:] == "600"
    assert not [f for f in os.listdir(tmp_path) if f.startswith(".tmp_")]
