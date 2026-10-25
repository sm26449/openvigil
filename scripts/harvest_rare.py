#!/usr/bin/env python3
"""Recolta tintita pentru clasele rare (caine, pisica) din inregistrari.

Snapshot-urile stiu CAND a fost fiecare detectie (numele fisierului contine ora si
eticheta); inregistrarile pastreaza 7 zile. Pentru fiecare moment cu dog/cat din
fereastra pastrata, extragem 3 cadre CURATE (t-1s, t, t+1s) din segmentul respectiv,
in data/dataset, cu numele standard — constructorul de dataset le adnoteaza apoi cu
teacher-ul ca pe oricare altele. 9 instante de caine devin cateva sute.

Rulare:  python scripts/harvest_rare.py
"""
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))
import record  # noqa: E402

SNAP = os.path.join(ROOT, "data", "snapshots")
DS = os.path.join(ROOT, "data", "dataset")
LABELS = ("dog", "cat")
OFFSETS = (-1, 0, 1)


def main():
    made, lipsa = 0, 0
    for day in sorted(os.listdir(SNAP)):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", day):
            continue
        for fn in sorted(os.listdir(os.path.join(SNAP, day))):
            m = re.match(r"^(\d{6})_cam(\d+)_(\w+)\.jpg$", fn)
            if not m or m[3] not in LABELS:
                continue
            hh, mm, ss = m[1][:2], m[1][2:4], m[1][4:]
            cam = int(m[2])
            t = time.mktime(time.strptime(f"{day} {hh}:{mm}:{ss}",
                                          "%Y-%m-%d %H:%M:%S"))
            files = record._files(cam)
            seg = next(((st, p) for st, p, _ in reversed(files)
                        if st <= t <= st + record.SEGMENT + 30), None)
            if seg is None:
                lipsa += 1
                continue
            for off in OFFSETS:
                stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(t + off))
                out = os.path.join(DS, f"cam{cam}-{stamp}.jpg")
                if os.path.exists(out):
                    continue
                r = subprocess.run(
                    ["ffmpeg", "-nostdin", "-loglevel", "error",
                     "-ss", str(max(0, t + off - seg[0])), "-i", seg[1],
                     "-frames:v", "1", "-q:v", "2", out],
                    capture_output=True, timeout=30)
                if r.returncode == 0 and os.path.exists(out):
                    made += 1
    print(f"[harvest] {made} cadre noi in data/dataset "
          f"({lipsa} momente fara inregistrare pastrata)", flush=True)


if __name__ == "__main__":
    main()
