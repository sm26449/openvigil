#!/usr/bin/env python3
"""Evaluarea unui model pe setul de validare FIX (data/finetune/images/val), pe clase.

mAP-ul global ascunde ce conteaza: „om” e clasa care da alarme, „porumbel” nu. Scriem
precizia, recall-ul si mAP50 pentru fiecare clasa, plus cifrele globale, in
runs/<nume>/evaluare.json (sau unde se cere), ca interfata sa compare modelele intre ele.

Modelul poate avea alte clase decat datasetul (curte-v1 stie 11, datasetul cu feedback poate
avea 13): evaluam pe clasele comune — etichetele de val se filtreaza si se renumeroteaza in
data/finetune/eval_tmp/<eticheta>/ (hardlink-uri la imagini, nu copii).

  venv/bin/python scripts/evalueaza.py --model data/finetune/runs/curte-v2/weights/best.pt
"""
import argparse
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "finetune")


def _clase_dataset():
    nume = {}
    try:
        with open(os.path.join(OUT, "dataset.yaml")) as f:
            in_names = False
            for ln in f:
                if ln.startswith("names:"):
                    in_names = True
                    continue
                if in_names and ln.startswith("  ") and ":" in ln:
                    i, n = ln.strip().split(":", 1)
                    nume[int(i)] = n.strip()
    except OSError:
        pass
    return nume


def pregateste_val(nume_model, eticheta):
    """Datasetul de evaluare pentru un model cu clasele `nume_model` ({id: nume}):
    val-ul fix, cu etichetele filtrate/renumerotate la clasele modelului. Intoarce calea yaml."""
    ds = _clase_dataset()
    ds_inv = {n: i for i, n in ds.items()}
    model_inv = {n: i for i, n in nume_model.items()}
    harta = {ds_inv[n]: model_inv[n] for n in model_inv if n in ds_inv}   # id dataset -> id model
    d = os.path.join(OUT, "eval_tmp", eticheta)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "images", "val"))
    os.makedirs(os.path.join(d, "labels", "val"))
    src_i = os.path.join(OUT, "images", "val")
    src_l = os.path.join(OUT, "labels", "val")
    n = 0
    for fn in os.listdir(src_i):
        try:
            os.link(os.path.join(src_i, fn), os.path.join(d, "images", "val", fn))
        except OSError:
            shutil.copyfile(os.path.join(src_i, fn), os.path.join(d, "images", "val", fn))
        linii = []
        try:
            with open(os.path.join(src_l, os.path.splitext(fn)[0] + ".txt")) as f:
                for ln in f:
                    p = ln.split()
                    if len(p) >= 5 and int(p[0]) in harta:
                        linii.append(" ".join([str(harta[int(p[0])]), *p[1:5]]))
        except OSError:
            pass
        with open(os.path.join(d, "labels", "val", os.path.splitext(fn)[0] + ".txt"), "w") as f:
            f.write("\n".join(linii) + ("\n" if linii else ""))
        n += 1
    yaml = os.path.join(d, "dataset.yaml")
    with open(yaml, "w") as f:
        f.write(f"path: {d}\ntrain: images/val\nval: images/val\nnames:\n"
                + "".join(f"  {i}: {nume_model[i]}\n" for i in sorted(nume_model)))
    return yaml, n


def evalueaza(cale_model, eticheta=None, imgsz=640, batch=16):
    from ultralytics import YOLO
    m = YOLO(cale_model)
    nume = {int(i): str(n) for i, n in m.names.items()}
    eticheta = eticheta or os.path.basename(os.path.dirname(os.path.dirname(cale_model))) or "model"
    yaml, n_img = pregateste_val(nume, eticheta)
    r = m.val(data=yaml, imgsz=imgsz, batch=batch, device=0, plots=False, verbose=False, split="val",
              project=os.path.join(OUT, "eval_tmp"), name=eticheta + "-val", exist_ok=True)
    box = r.box
    clase = {}
    for k, i in enumerate(getattr(box, "ap_class_index", [])):
        p, rc, ap50, ap = box.class_result(k)
        clase[nume[int(i)]] = {"p": round(float(p), 3), "r": round(float(rc), 3),
                               "map50": round(float(ap50), 3), "map5095": round(float(ap), 3)}
    rez = {"model": os.path.relpath(cale_model, ROOT), "imagini_val": n_img,
           "global": {"p": round(float(box.mp), 3), "r": round(float(box.mr), 3),
                      "map50": round(float(box.map50), 3), "map5095": round(float(box.map), 3)},
           "clase": clase}
    shutil.rmtree(os.path.join(OUT, "eval_tmp", eticheta), ignore_errors=True)
    shutil.rmtree(os.path.join(OUT, "eval_tmp", eticheta + "-val"), ignore_errors=True)
    return rez


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--iesire", help="fisierul JSON (implicit: runs/<nume>/evaluare.json langa model)")
    a = ap.parse_args()
    rez = evalueaza(a.model)
    iesire = a.iesire or os.path.join(os.path.dirname(os.path.dirname(a.model)), "evaluare.json")
    os.makedirs(os.path.dirname(iesire), exist_ok=True)
    with open(iesire, "w", encoding="utf-8") as f:
        json.dump(rez, f, ensure_ascii=False, indent=1)
    g = rez["global"]
    print(f"[eval] global P={g['p']} R={g['r']} mAP50={g['map50']} mAP50-95={g['map5095']} "
          f"({rez['imagini_val']} imagini)", flush=True)
    for c, v in sorted(rez["clase"].items()):
        print(f"[eval] {c:28s} P={v['p']:.3f} R={v['r']:.3f} mAP50={v['map50']:.3f}", flush=True)
    print(f"[eval] scris {os.path.relpath(iesire, ROOT)}", flush=True)


if __name__ == "__main__":
    main()
