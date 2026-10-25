#!/usr/bin/env python3
"""Construieste datasetul de fine-tune din cadrele proprii (data/dataset).

1. Teacher-ul (yolo11x, cel mai bun model COCO) adnoteaza automat toate cadrele.
2. Pasarile se despart pe specii cu SigLIP zero-shot (gaina / porumbel / pasare) —
   avem camerele de gaini, deci "bird" generic ar fi irosit exact avantajul local.
3. Split stratificat 85/15 pe camera -> data/finetune/{images,labels}/{train,val}
   + dataset.yaml. Setul de VAL ramane fix (seed 42): el e harnass-ul de evaluare
   pentru orice model viitor.
Cadrele fara nicio detectie se pastreaza cu etichete goale — sunt fundalul care
invata modelul sa NU vada fantome (stalpul portii de pe cam7 e in ele).

Rulare:  python scripts/finetune_dataset.py
"""
import collections
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))

DS = os.path.join(ROOT, "data", "dataset")
OUT = os.path.join(ROOT, "data", "finetune")
CLASSES = ["person", "car", "truck", "bus", "motorcycle",
           "cat", "dog", "horse", "bird", "chicken", "pigeon"]
COCO_MAP = {0: "person", 2: "car", 7: "truck", 5: "bus", 3: "motorcycle",
            15: "cat", 16: "dog", 17: "horse", 14: "bird"}
TEACHER_CONF = 0.55     # sub atat, eticheta automata e prea nesigura ca sa invatam din ea
VAL_FRACTION = 0.15
BIRD_PROMPTS = [("chicken", "o gaina in curte"), ("pigeon", "un porumbel"),
                ("bird", "o pasare salbatica mica")]


def main():
    import cv2
    import numpy as np
    from PIL import Image
    from ultralytics import YOLO
    import semantic

    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0,
                    help="cel mult atatea cadre (esantion aleator, seed fix); 0 = toate")
    arg = ap.parse_args()
    teacher = YOLO("yolo11x.pt")   # se descarca singur la prima rulare
    files = sorted(f for f in os.listdir(DS) if f.endswith(".jpg"))
    # cadrele verificate de om sunt cele mai valoroase: intra mereu, esantionam doar restul
    import json as _json
    try:
        with open(os.path.join(OUT, "verificate.json")) as f:
            verif = set(_json.load(f))
    except (OSError, ValueError):
        verif = set()
    if arg.max and len(files) > arg.max:
        random.seed(42)
        fixe = [f for f in files if f in verif]
        rest = [f for f in files if f not in verif]
        files = sorted(fixe + random.sample(rest, max(0, arg.max - len(fixe))))
    print(f"[dataset] {len(files)} cadre de adnotat cu teacher yolo11x", flush=True)

    # embeddings pentru prompturile de specii, o singura data
    bird_emb = {lbl: semantic.embed_text(txt) for lbl, txt in BIRD_PROMPTS}

    # cadrele verificate de om in editor (data/finetune/verificate.json) isi pastreaza eticheta
    import json
    verificate = {}
    try:
        with open(os.path.join(OUT, "verificate.json")) as f:
            verificate = json.load(f)
    except (OSError, ValueError):
        pass
    pastrate = {}
    for sp in ("train", "val"):
        d = os.path.join(OUT, "labels", sp)
        for fn in verificate:
            p = os.path.join(d, fn[:-4] + ".txt")
            if os.path.isfile(p):
                with open(p) as f:
                    pastrate[fn] = [tuple(float(x) if i else int(x) for i, x in enumerate(l.split()))
                                    for l in f if len(l.split()) >= 5]
    if pastrate:
        print(f"[dataset] {len(pastrate)} cadre verificate de om isi pastreaza etichetele", flush=True)
    labels = {}          # fn -> [(cls_id, cx, cy, w, h)]
    counts = collections.Counter()
    for i, fn in enumerate(files):
        path = os.path.join(DS, fn)
        img = cv2.imread(path)
        if img is None:
            continue
        h, w = img.shape[:2]
        if fn in pastrate:
            labels[fn] = pastrate[fn]
            for row in pastrate[fn]:
                counts[CLASSES[row[0]]] += 1
            continue
        r = teacher.predict(path, imgsz=640, conf=TEACHER_CONF,
                            classes=list(COCO_MAP), device=0, verbose=False)[0]
        rows = []
        for b in r.boxes:
            lbl = COCO_MAP.get(int(b.cls))
            if not lbl:
                continue
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            if lbl == "bird":
                # specia, cu SigLIP zero-shot pe crop
                mx = (x2 - x1) * 0.15
                my = (y2 - y1) * 0.15
                crop = img[max(0, int(y1 - my)):min(h, int(y2 + my)),
                           max(0, int(x1 - mx)):min(w, int(x2 + mx))]
                if crop.size:
                    pil = Image.fromarray(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
                    emb = semantic.embed_images([pil])[0]
                    scoruri = {l: float(emb @ e) for l, e in bird_emb.items()}
                    lbl = max(scoruri, key=scoruri.get)
            cid = CLASSES.index(lbl)
            rows.append((cid, (x1 + x2) / 2 / w, (y1 + y2) / 2 / h,
                         (x2 - x1) / w, (y2 - y1) / h))
            counts[lbl] += 1
        labels[fn] = rows
        if (i + 1) % 500 == 0:
            print(f"[dataset] {i + 1}/{len(files)}", flush=True)

    # split stratificat pe camera, seed fix — val-ul ramane acelasi pentru totdeauna
    random.seed(42)
    by_cam = collections.defaultdict(list)
    for fn in labels:
        by_cam[fn.split("-")[0]].append(fn)
    train, val = [], []
    for cam, lst in sorted(by_cam.items()):
        random.shuffle(lst)
        k = max(1, int(len(lst) * VAL_FRACTION))
        val += lst[:k]
        train += lst[k:]

    import shutil
    for split, lst in (("train", train), ("val", val)):
        idir = os.path.join(OUT, "images", split)
        ldir = os.path.join(OUT, "labels", split)
        # datasetul se INLOCUIESTE: altfel un cadru mutat de la o impartire la alta ar ramane
        # si in train si in val (scurgere train/val, mAP umflat), iar pozele scoase ar persista
        shutil.rmtree(idir, ignore_errors=True)
        shutil.rmtree(ldir, ignore_errors=True)
        os.makedirs(idir, exist_ok=True)
        os.makedirs(ldir, exist_ok=True)
        for fn in lst:
            dst = os.path.join(idir, fn)
            src = os.path.join(DS, fn)
            if os.path.exists(src):
                os.link(src, dst)   # hardlink, nu copie
            else:
                continue
            with open(os.path.join(ldir, fn[:-4] + ".txt"), "w") as f:
                for row in labels[fn]:
                    f.write(f"{row[0]} {row[1]:.6f} {row[2]:.6f} "
                            f"{row[3]:.6f} {row[4]:.6f}\n")

    with open(os.path.join(OUT, "dataset.yaml"), "w") as f:
        f.write(f"path: {OUT}\ntrain: images/train\nval: images/val\n"
                f"names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(CLASSES)))

    goale = sum(1 for r in labels.values() if not r)
    print(f"\n[dataset] gata: {len(train)} train / {len(val)} val "
          f"({goale} cadre-fundal, fara obiecte)", flush=True)
    for lbl, n in counts.most_common():
        print(f"   {lbl:12s} {n}", flush=True)


if __name__ == "__main__":
    main()
