#!/usr/bin/env python3
"""Punct de pornire pentru nvr-web.

server.py e importat ca MODUL ('server'), nu rulat ca __main__ — asa blueprint-urile
(routes_*.py) pot face `from server import <helper>` fara sa reimporte dublu modulul.
Systemd ruleaza acest fisier; el importa server (care isi inregistreaza blueprint-urile
la import) si porneste bucla web."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import server

if __name__ == "__main__":
    server.main()
