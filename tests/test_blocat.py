"""record.blocat(): in directorul de inregistrari nu se poate scrie (ENOKEY/EACCES/EROFS)."""
import errno
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import record  # noqa: E402


def test_director_normal_nu_e_blocat(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "REC_DIR", str(tmp_path))
    assert record.blocat() is False
    assert not os.path.exists(tmp_path / ".proba-cheie")   # proba se curata


def test_director_lipsa_nu_e_blocat(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "REC_DIR", str(tmp_path / "nu-exista"))
    assert record.blocat() is False


def test_enokey_inseamna_blocat(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "REC_DIR", str(tmp_path))

    def open_fara_cheie(*a, **k):
        raise OSError(errno.ENOKEY, "Required key not available")
    monkeypatch.setattr(record.os, "open", open_fara_cheie)
    assert record.blocat() is True


def test_eacces_inseamna_blocat(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "REC_DIR", str(tmp_path))

    def open_fara_drept(*a, **k):
        raise OSError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(record.os, "open", open_fara_drept)
    assert record.blocat() is True


def test_alta_eroare_nu_e_blocat(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "REC_DIR", str(tmp_path))

    def open_plin(*a, **k):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(record.os, "open", open_plin)
    assert record.blocat() is False
