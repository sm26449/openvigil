#!/usr/bin/env python3
"""Aduce modelele de care are nevoie nvr-ai inainte de prima pornire si spune ce lipseste.

Rulat de containerul `ai` la fiecare pornire (ieftin cand totul e deja pe disc):
  - YOLO11m (Ultralytics, AGPL-3.0): `data/yolo11m_stock.pt` (modelul de referinta) si
    `data/yolo11n.pt` (slotul activ; numele e istoric, continutul e cel antrenat sau stock);
  - InsightFace buffalo_l, Qwen2.5-VL, SigLIP se descarca singure la prima folosire, in
    volumul `models/` (HF_HOME) respectiv `data/faces/insightface`;
  - OSNet (re-ID dupa corp) nu are o sursa automata: fara `data/osnet_x0_25_msmt17.onnx`
    functia ramane oprita, iar aici doar o spunem.
"""
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
STOCK = os.path.join(DATA, "yolo11m_stock.pt")
ACTIV = os.path.join(DATA, "yolo11n.pt")
OSNET = os.path.join(DATA, "osnet_x0_25_msmt17.onnx")


def yolo():
    if os.path.isfile(STOCK) and os.path.isfile(ACTIV):
        return
    os.makedirs(DATA, exist_ok=True)
    print("[modele] descarc YOLO11m (Ultralytics, AGPL-3.0)...", flush=True)
    from ultralytics.utils.downloads import attempt_download_asset
    cwd = os.getcwd()
    os.chdir(DATA)
    try:
        cale = attempt_download_asset("yolo11m.pt")
    finally:
        os.chdir(cwd)
    cale = os.path.join(DATA, os.path.basename(str(cale)))
    if not os.path.isfile(STOCK):
        shutil.move(cale, STOCK)
    elif os.path.exists(cale):
        os.remove(cale)
    if not os.path.isfile(ACTIV):
        shutil.copy2(STOCK, ACTIV)
    print(f"[modele] YOLO11m la {STOCK} (activ: {ACTIV})", flush=True)


def main():
    try:
        yolo()
    except Exception as e:   # fara internet la prima pornire: nvr-ai porneste si spune singur
        print(f"[modele] YOLO nu s-a putut descarca: {e}", file=sys.stderr)
    if not os.path.isfile(OSNET):
        print("[modele] re-ID dupa corp e oprit: lipseste data/osnet_x0_25_msmt17.onnx "
              "(export ONNX al OSNet x0.25 msmt17 din torchreid; vezi docs/nvr-ai.md)", flush=True)


if __name__ == "__main__":
    main()
