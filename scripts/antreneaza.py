#!/usr/bin/env python3
"""Antreneaza (fine-tune) modelul de detectie pe datasetul din data/finetune, cu ultralytics,
pe GPU. Tipareste o linie pe epoca ("[train] epoca i/N mAP50=..") si la final
"[train] gata best=<cale>", ca nvr-ai (app/antrenare.py) sa arate progresul in interfata.

  venv/bin/python scripts/antreneaza.py --nume curte-v2 --epoci 60 [--baza m|l|<cale.pt>] [--patience 10]
Reteta e cea a lui curte-v1 (2026-08-15): yolo11m, imgsz 640, batch 10, patience 8; `--baza l`
porneste de la yolo11l (mai precis, de ~2 ori mai lent la antrenare si la rulare). La final
modelul e evaluat pe clase pe val-ul fix (scripts/evalueaza.py -> runs/<nume>/evaluare.json).
"""
import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--nume", required=True)
    ap.add_argument("--epoci", type=int, default=30)
    ap.add_argument("--baza", default="m", help="m | l | calea unui .pt")
    ap.add_argument("--batch", type=int, default=0, help="0 = 10 pentru m, 6 pentru l")
    ap.add_argument("--patience", type=int, default=8)
    a = ap.parse_args()
    from ultralytics import YOLO
    data = os.path.join(ROOT, "data", "finetune", "dataset.yaml")
    if not os.path.isfile(data):
        print("[train] lipseste data/finetune/dataset.yaml — construieste intai datasetul", flush=True)
        sys.exit(2)
    baza = {"m": os.path.join(ROOT, "data", "yolo11m_stock.pt"), "l": "yolo11l.pt"}.get(a.baza, a.baza)
    if a.baza == "m" and not os.path.isfile(baza):
        baza = "yolo11m.pt"
    batch = a.batch or (6 if a.baza == "l" else 10)
    print(f"[train] baza {os.path.basename(baza)}, batch {batch}, patience {a.patience}", flush=True)
    model = YOLO(baza)

    def pe_epoca(trainer):
        if trainer.epoch + 1 > trainer.epochs:
            return                      # validarea finala pe best.pt declanseaza inca o data callback-ul
        m = trainer.metrics or {}
        map50 = m.get("metrics/mAP50(B)")
        extra = f" mAP50={map50:.3f}" if isinstance(map50, float) else ""
        print(f"[train] epoca {trainer.epoch + 1}/{trainer.epochs}{extra}", flush=True)

    model.add_callback("on_fit_epoch_end", pe_epoca)
    model.train(data=data, epochs=a.epoci, imgsz=640, batch=batch, patience=a.patience, device=0,
                workers=8, project=os.path.join(ROOT, "data", "finetune", "runs"), name=a.nume,
                exist_ok=False, verbose=False, plots=True)
    best = os.path.join(ROOT, "data", "finetune", "runs", a.nume, "weights", "best.pt")
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import evalueaza
        import json
        rez = evalueaza.evalueaza(best, eticheta=a.nume)
        with open(os.path.join(os.path.dirname(os.path.dirname(best)), "evaluare.json"), "w", encoding="utf-8") as f:
            json.dump(rez, f, ensure_ascii=False, indent=1)
        g = rez["global"]
        print(f"[train] evaluare: P={g['p']} R={g['r']} mAP50={g['map50']}", flush=True)
    except Exception as e:
        print(f"[train] evaluarea nu a mers: {str(e)[:120]}", flush=True)
    print(f"[train] gata best={best}", flush=True)


if __name__ == "__main__":
    main()
