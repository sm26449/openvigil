#!/usr/bin/env python3
"""Editorul de adnotare: citirea si scrierea etichetelor YOLO din data/finetune.

Datasetul e cel construit de scripts/finetune_dataset.py: images/{train,val}/<fn>.jpg si
labels/{train,val}/<fn>.txt (o linie per chenar: `cls cx cy w h`, normalizate 0..1; fisier gol =
cadru fara obiecte). Omul corecteaza chenarele din interfata; un cadru salvat intra in
data/finetune/verificate.json, iar reconstruirea datasetului NU mai suprascrie eticheta lui
(teacher-ul nu stie mai bine decat omul).
"""
import os
import re
import threading
import time

import store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FINETUNE = os.path.join(ROOT, "data", "finetune")
VERIFICATE = os.path.join(FINETUNE, "verificate.json")
SPLITS = ("train", "val")
_FN = re.compile(r"^[A-Za-z0-9._-]+\.jpg$")
_lock = threading.Lock()


def clase():
    """Numele claselor din dataset.yaml (indexul = id-ul din etichete)."""
    out = {}
    try:
        with open(os.path.join(FINETUNE, "dataset.yaml"), encoding="utf-8") as f:
            in_names = False
            for line in f:
                if line.startswith("names:"):
                    in_names = True
                    continue
                m = re.match(r"^\s+(\d+):\s*(\S+)", line) if in_names else None
                if m:
                    out[int(m[1])] = m[2]
                elif in_names and line.strip() and not line.startswith(" "):
                    break
    except OSError:
        pass
    return [out[i] for i in sorted(out)]


def verificate():
    return store.load_json(VERIFICATE, {}) or {}


def _cai(split, fn):
    if not _FN.match(fn or ""):
        raise ValueError("cadru invalid")
    if split == "nesigure":          # cadru brut din data/dataset, etichete din coada
        import nesigure
        return os.path.join(nesigure.DATASET_DIR, fn), None
    if split not in SPLITS:
        raise ValueError("cadru invalid")
    img = os.path.join(FINETUNE, "images", split, fn)
    lbl = os.path.join(FINETUNE, "labels", split, fn[:-4] + ".txt")
    return img, lbl


def img_path(split, fn):
    img, _ = _cai(split, fn)
    return img if os.path.isfile(img) else None


def citeste_boxes(lbl):
    boxes = []
    try:
        with open(lbl, encoding="utf-8") as f:
            for line in f:
                p = line.split()
                if len(p) >= 5:
                    boxes.append({"cls": int(p[0]), "cx": float(p[1]), "cy": float(p[2]),
                                  "w": float(p[3]), "h": float(p[4])})
    except OSError:
        pass
    return boxes


def lista(split=None, doar_neverificate=False, cam=None, offset=0, limit=60):
    """Cadrele datasetului, cele mai noi primele: [{fn, split, n, verificat}] + totaluri.
    Numarul de chenare vine din marimea fisierului de etichete (0 octeti = fara obiecte),
    ca listarea a 7.000 de cadre sa nu citeasca 7.000 de fisiere.
    split="nesigure": coada cadrelor nesigure (cele mai nesigure primele)."""
    ver = verificate()
    if split == "nesigure":
        import nesigure
        q = nesigure.incarca()
        rows = [{"fn": fn, "split": "nesigure", "n": len(v.get("boxes", [])), "verificat": False,
                 "scor": v.get("scor", 0)} for fn, v in q.items()
                if not cam or fn.startswith(f"cam{cam}-")]
        rows.sort(key=lambda r: (-r["scor"], r["fn"]), reverse=False)
        return {"cadre": rows[offset:offset + limit], "total": len(rows), "verificate": len(ver),
                "clase": clase(), "nesigure": len(q)}
    rows = []
    for sp in ([split] if split in SPLITS else SPLITS):
        d = os.path.join(FINETUNE, "images", sp)
        for fn in (os.listdir(d) if os.path.isdir(d) else []):
            if not fn.endswith(".jpg"):
                continue
            if cam and not fn.startswith(f"cam{cam}-"):
                continue
            v = fn in ver
            if doar_neverificate and v:
                continue
            lbl = os.path.join(FINETUNE, "labels", sp, fn[:-4] + ".txt")
            try:
                n = -1 if not os.path.isfile(lbl) else (0 if os.path.getsize(lbl) == 0 else None)
            except OSError:
                n = -1
            rows.append({"fn": fn, "split": sp, "n": n, "verificat": v})
    # cam<id>-YYYYMMDD-HHMMSS.jpg: cele mai noi primele inseamna dupa data, nu dupa camera
    rows.sort(key=lambda r: r["fn"].split("-", 1)[1] if "-" in r["fn"] else r["fn"], reverse=True)
    total = len(rows)
    rows = rows[offset:offset + limit]
    for r in rows:
        if r["n"] is None:
            r["n"] = len(citeste_boxes(os.path.join(FINETUNE, "labels", r["split"], r["fn"][:-4] + ".txt")))
    import nesigure
    return {"cadre": rows, "total": total, "verificate": len(ver), "clase": clase(),
            "nesigure": len(nesigure.incarca())}


def cadru(split, fn):
    """Un cadru cu etichetele lui: {fn, split, w, h, boxes, verificat, clase}."""
    img, lbl = _cai(split, fn)
    if not os.path.isfile(img):
        raise FileNotFoundError(fn)
    import cv2
    im = cv2.imread(img)
    h, w = (im.shape[:2] if im is not None else (0, 0))
    if split == "nesigure":
        import nesigure
        q = nesigure.incarca().get(fn)
        if q is None:
            raise FileNotFoundError(fn)
        boxes = q.get("boxes", [])
    else:
        boxes = citeste_boxes(lbl)
    return {"fn": fn, "split": split, "w": w, "h": h, "boxes": boxes,
            "verificat": verificate().get(fn), "clase": clase()}


def salveaza(split, fn, boxes, cine=None):
    """Scrie etichetele (atomic) si marcheaza cadrul ca verificat de om. Un cadru din coada
    „nesigure” intra intai in dataset (images/train), apoi iese din coada."""
    img, lbl = _cai(split, fn)
    if not os.path.isfile(img):
        raise FileNotFoundError(fn)
    if split == "nesigure":
        import shutil
        import nesigure
        dst = os.path.join(FINETUNE, "images", "train", fn)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not os.path.exists(dst):
            shutil.copy2(img, dst)
        rez = salveaza("train", fn, boxes, cine=cine)
        nesigure.scoate(fn)
        return rez
    n_clase = len(clase())
    linii = []
    for b in boxes or []:
        try:
            c = int(b["cls"]); cx, cy, w, h = (float(b[k]) for k in ("cx", "cy", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise ValueError("chenar invalid")
        if not (0 <= c < n_clase) or not all(0.0 <= v <= 1.0 for v in (cx, cy, w, h)) or w <= 0 or h <= 0:
            raise ValueError("chenar în afara cadrului sau clasă necunoscută")
        linii.append(f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")
    os.makedirs(os.path.dirname(lbl), exist_ok=True)
    with _lock:
        store.atomic_write_text(lbl, ("\n".join(linii) + "\n") if linii else "", mode=0o644)
        ver = verificate()
        ver[fn] = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "cine": cine, "n": len(linii)}
        store.atomic_write_json(VERIFICATE, ver, mode=0o644, indent=0)
    return {"ok": True, "n": len(linii), "verificat": ver[fn]}
