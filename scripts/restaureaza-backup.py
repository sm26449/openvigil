#!/usr/bin/env python3
"""Reface starea NVR-ului dintr-o copie criptata (backup_nocturn.py).

  venv/bin/python scripts/restaureaza-backup.py <fisier.tar.gz.enc> <destinatie> [--cheie CHEIE]

Cheia: din data/backup_key daca exista, altfel --cheie (cea notata din Setari -> Sistem).
Destinatia e un director: pentru refacere reala, opreste serviciile (systemctl stop nvr-web
nvr-ai nvr-record), da `data/` ca destinatie (fisierele existente se suprascriu) si
reporneste-le. Pentru o proba, orice director gol.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))
import backup_nocturn as bn  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("fisier")
ap.add_argument("destinatie")
ap.add_argument("--cheie", default=None)
a = ap.parse_args()
k = a.cheie or bn.cheie(genereaza=False)
if not k:
    sys.exit("lipseste cheia: da --cheie (data/backup_key nu exista aici)")
manifest = None
mp = a.fisier[:-len(".tar.gz.enc")] + ".json"
if os.path.exists(mp):
    manifest = json.load(open(mp))
fis = bn.restaureaza(a.fisier, k, a.destinatie, manifest)
print(f"refacute {len(fis)} fisiere in {a.destinatie}")
for f in fis[:20]:
    print("  ", f)
if len(fis) > 20:
    print(f"   ... si inca {len(fis) - 20}")
