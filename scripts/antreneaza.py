#!/usr/bin/env python3
"""Antreneaza (fine-tune) modelul de detectie pe datasetul din data/finetune, cu ultralytics,
pe GPU. Tipareste o linie pe epoca ("[train] epoca i/N mAP50=..") si la final
"[train] gata best=<cale>", ca nvr-ai (app/antrenare.py) sa arate progresul in interfata.

  venv/bin/python scripts/antreneaza.py --nume curte-v2 --epoci 30 [--baza data/yolo11m_stock.pt]
Reteta e cea a lui curte-v1 (2026-08-15): yolo11m, imgsz 640, batch 10, patience 8.
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nume", required=True)
    ap.add_argument("--epoci", type=int, default=30)
    ap.add_argument("--baza", default=os.path.join(ROOT, "data", "yolo11m_stock.pt"))
    ap.add_argument("--batch", type=int, default=10)
    a = ap.parse_args()
    from ultralytics import YOLO
    data = os.path.join(ROOT, "data", "finetune", "dataset.yaml")
    if not os.path.isfile(data):
        print("[train] lipseste data/finetune/dataset.yaml — construieste intai datasetul", flush=True)
        sys.exit(2)
    model = YOLO(a.baza if os.path.isfile(a.baza) else "yolo11m.pt")

    def pe_epoca(trainer):
        m = trainer.metrics or {}
        map50 = m.get("metrics/mAP50(B)")
        extra = f" mAP50={map50:.3f}" if isinstance(map50, float) else ""
        print(f"[train] epoca {trainer.epoch + 1}/{trainer.epochs}{extra}", flush=True)

    model.add_callback("on_fit_epoch_end", pe_epoca)
    model.train(data=data, epochs=a.epoci, imgsz=640, batch=a.batch, patience=8, device=0,
                workers=8, project=os.path.join(ROOT, "data", "finetune", "runs"), name=a.nume,
                exist_ok=False, verbose=False, plots=True)
    best = os.path.join(ROOT, "data", "finetune", "runs", a.nume, "weights", "best.pt")
    print(f"[train] gata best={best}", flush=True)


if __name__ == "__main__":
    main()
