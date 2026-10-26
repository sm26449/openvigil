#!/usr/bin/env python3
"""Jurnalul proceselor (nvr-web, nvr-ai, nvr-record): `logging` cu niveluri in loc de print().

Mesajele pastreaza eticheta modulului in text („[record] ...”), ca pana acum, deci
`journalctl -u nvr-web` se citeste la fel; in plus fiecare are nivel (INFO / WARNING / ERROR)
si poate fi filtrat: NVR_LOG_LEVEL=WARNING in unitatea systemd arata doar problemele.
Scrie pe stderr (journald il preia cu unitatea), fara fisiere proprii.
"""
import logging
import os
import sys

_logger = logging.getLogger("nvr")
if not _logger.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(h)
    _logger.setLevel(getattr(logging, (os.environ.get("NVR_LOG_LEVEL") or "INFO").upper(), logging.INFO))
    _logger.propagate = False


import re as _re


def fara_parole(text):
    """ffmpeg/ffprobe repeta URL-ul de intrare in erori („rtsp://user:parola@…”); textul ajunge in
    mesaje de job, in jurnal si pe ecran — parola camerei nu are ce cauta acolo."""
    if isinstance(text, bytes):
        text = text.decode(errors="ignore")
    return _re.sub(r"://[^/@\s]+@", "://***@", text or "")


def info(*parti):
    _logger.info(" ".join(str(p) for p in parti))


def warning(*parti):
    _logger.warning(" ".join(str(p) for p in parti))


def error(*parti):
    _logger.error(" ".join(str(p) for p in parti))


def debug(*parti):
    _logger.debug(" ".join(str(p) for p in parti))
