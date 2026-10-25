#!/usr/bin/env python3
"""Scriere JSON ATOMICA — o singura implementare pentru tot ce e configurare/stare.

Atomic = scriem intr-un fisier temporar pe ACELASI director (deci acelasi filesystem),
fsync, apoi os.replace (rename atomic pe POSIX). Un cititor vede fie versiunea veche
INTREAGA, fie cea noua INTREAGA — niciodata jumatate de JSON. Un crash / kill -9 / disc
plin la mijlocul scrierii lasa fisierul original NEATINS (tmp-ul e sters), nu gol.

Inainte, `open(path, "w")` / `O_TRUNC` truncau fisierul real de la primul byte: o cadere
in timpul scrierii lasa settings.json / cameras.json / users.json gol -> sistem fara
camere, fara token-uri, fara cont. De-aici acest modul.
"""
import json
import os
import tempfile


def load_json(path, default=None):
    """Citire toleranta: lipsa fisierului, permisiuni gresite (OSError) sau JSON invalid
    (ValueError) -> `default`. Un fisier de stare corupt/inaccesibil NU trebuie sa doboare
    pornirea — mai bine porneste pe implicit decat sa crape."""
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def atomic_write_text(path, text, mode=0o600):
    """Acelasi contract ca atomic_write_json, pentru text (yaml, jsonl)."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".txt")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data, mode=0o600, **dump_kwargs):
    """Scrie `data` ca JSON in `path`, atomic. `mode` = permisiuni finale (implicit 0o600,
    fisierele de configurare au secrete). Restul kwargs merg la json.dump."""
    dump_kwargs.setdefault("ensure_ascii", False)
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, **dump_kwargs)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)          # rename atomic
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
