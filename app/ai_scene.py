#!/usr/bin/env python3
"""Detector de obiect NOU aparut / DISPARUT pe camere statice.

Ideea: pe o camera fixa, fundalul (scena goala) e stabil. Un obiect LASAT sau LUAT
schimba o regiune mica si o tine schimbata mult timp — spre deosebire de o masina sau
un om, care trec. Deci: mediana lenta a scenei (miscarea dispare din ea), comparata cu o
referinta; o regiune diferita, mica, care PERSISTA peste un prag — si care NU se suprapune
peste un obiect in miscare vazut de tracker — inseamna obiect nou/disparut. Emitem un
eveniment (obiect_nou / obiect_disparut), care ajunge in feed-ul de incidente cu ora + clip.

Robustete:
- schimbarile GLOBALE de lumina (nori, zi->noapte) ating tot cadrul -> daca prea mult din
  cadru s-a schimbat, e lumina, nu obiect: absorbim rapid in referinta si uitam candidatii;
- driftul LENT de lumina il urmarim cu un EMA, dar DOAR in zonele nemodificate; zonele in
  schimbare raman "inghetate" pana se rezolva, ca sa nu fie mancate de EMA inainte sa apuce
  sa persiste;
- obiectele in miscare (tracker) sunt sterse din masca, deci o masina care trece nu conteaza.
"""
import time
from collections import deque

import cv2
import numpy as np

W = 480                 # latimea de lucru (procesam pe gray mic — ieftin)
SAMPLE_SEC = 20         # cat de des luam o mostra din flux
WINDOW = 12             # mostre pentru mediana curenta (~4 min la SAMPLE_SEC=20)
MIN_SAMPLES = 6
THRESH = 32             # diferenta de gri peste care un pixel e "schimbat"
MIN_AREA = 60           # px^2 la W=480 (obiect prea mic = zgomot)
MAX_AREA_FRAC = 0.06    # obiect prea mare (masina/umbra) nu ne intereseaza aici
BIG_CHANGE_FRAC = 0.15  # peste atat din cadru schimbat = lumina globala, nu obiect
CELL = 40               # cuantizarea centrului, ca aceeasi regiune sa se potriveasca intre tick-uri
EMA = 0.05              # cat de repede urmarim driftul lent de lumina in zonele stabile
DROP_AFTER = 2.5        # un candidat nevazut atatea mostre e uitat (obiectul a plecat)
REPORT_COOLDOWN = 3600  # nu re-raporta aceeasi regiune mai des de o ora


def _prep(frame_bgr):
    h, w = frame_bgr.shape[:2]
    g = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (W, max(1, int(h * W / w))))
    return cv2.GaussianBlur(g, (5, 5), 0)


def _classify(cur, refi, box):
    """Nou vs disparut: un obiect nou adauga muchii/textura in `cur`; unul disparut le
    scoate (erau in referinta). Comparam densitatea de muchii pe regiune."""
    x, y, w, h = box
    ec = cv2.Canny(cur[y:y + h, x:x + w], 50, 150).mean()
    er = cv2.Canny(refi[y:y + h, x:x + w], 50, 150).mean()
    return "disparut" if er > ec * 1.3 else "nou"


class SceneWatcher:
    """Starea unei camere. feed(cadru, boxuri_miscare, now) -> lista de evenimente."""
    def __init__(self, cam_id, persist_sec=1200, ignore_top=0.35):
        self.cam_id = cam_id
        self.persist_sec = persist_sec
        # Banda de SUS a cadrului (cer, linie de copaci, acoperisuri) e sursa principala de
        # fals-pozitive pe camerele de curte inclinate in jos: nori, soare, vant in frunze
        # schimba lumina fara sa fie "obiect lasat". O ignoram — obiectele de pe sol sunt jos.
        self.ignore_top = ignore_top
        self.ref = None                    # referinta scenei (float32, pt. EMA fin)
        self.samples = deque(maxlen=WINDOW)
        self.last_sample = 0.0
        self.pending = {}                  # key -> {since, box, seen, kind}
        self.reported = {}                 # key -> t (anti-repetare)

    def _moving_mask(self, boxes, H, Wd, orig_wh):
        m = np.zeros((H, Wd), np.uint8)
        ow, oh = orig_wh
        if not len(boxes) or not ow or not oh:
            return m
        sx, sy = Wd / ow, H / oh
        for b in boxes:
            x1, y1, x2, y2 = [float(v) for v in b[:4]]
            X1, Y1 = int(x1 * sx) - 5, int(y1 * sy) - 5
            X2, Y2 = int(x2 * sx) + 5, int(y2 * sy) + 5
            cv2.rectangle(m, (max(0, X1), max(0, Y1)),
                          (min(Wd, X2), min(H, Y2)), 255, -1)
        return m

    def feed(self, frame_bgr, moving_boxes, now):
        if now - self.last_sample < SAMPLE_SEC:
            return []
        self.last_sample = now
        g = _prep(frame_bgr)
        self.samples.append(g)
        if len(self.samples) < MIN_SAMPLES:
            return []
        cur = np.median(np.stack(self.samples), axis=0).astype(np.uint8)
        if self.ref is None or self.ref.shape != cur.shape:
            self.ref = cur.astype(np.float32)
            return []
        H, Wd = cur.shape
        refi = self.ref.astype(np.uint8)
        mask = (cv2.absdiff(cur, refi) > THRESH).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
        orig_wh = (frame_bgr.shape[1], frame_bgr.shape[0])
        mask[self._moving_mask(moving_boxes, H, Wd, orig_wh) > 0] = 0
        top = int(H * self.ignore_top)     # zona de interes: doar sub banda de sus (solul)
        if top > 0:
            mask[:top] = 0

        # fractia schimbata se masoara DOAR pe zona de interes (altfel banda de sus taiata
        # ar dilua mereu procentul si garda de lumina globala n-ar mai declansa)
        roi_px = H * Wd - top * Wd
        if roi_px > 0 and float((mask > 0).sum()) / roi_px > BIG_CHANGE_FRAC:
            self.ref = 0.5 * self.ref + 0.5 * cur    # lumina globala -> absorb rapid
            self.pending.clear()
            return []

        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        frozen = np.zeros((H, Wd), bool)
        events, seen = [], set()
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            area = w * h
            if area < MIN_AREA or area > MAX_AREA_FRAC * H * Wd:
                continue
            frozen[y:y + h, x:x + w] = True
            cx, cy = x + w // 2, y + h // 2
            key = (cx // CELL, cy // CELL)
            seen.add(key)
            if now - self.reported.get(key, -1e9) < REPORT_COOLDOWN:
                continue
            p = self.pending.get(key)
            if not p:
                self.pending[key] = {"since": now, "box": (x, y, w, h), "seen": now,
                                     "kind": _classify(cur, refi, (x, y, w, h))}
                continue
            p["seen"] = now
            p["box"] = (x, y, w, h)
            if now - p["since"] >= self.persist_sec:
                events.append(self._event(p, cx / Wd, cy / H, now))
                self.reported[key] = now
                self.ref[y:y + h, x:x + w] = cur[y:y + h, x:x + w]   # absorb -> nu re-fire
                del self.pending[key]

        for key in list(self.pending):
            if key not in seen and now - self.pending[key]["seen"] > DROP_AFTER * SAMPLE_SEC:
                del self.pending[key]

        stable = ~frozen
        self.ref[stable] = (1 - EMA) * self.ref[stable] + EMA * cur[stable]
        return events

    def _event(self, p, cx_rel, cy_rel, now):
        tip = "obiect_nou" if p["kind"] == "nou" else "obiect_disparut"
        return {"tip": tip, "date": {
            "fel": p["kind"],
            "unde": f"{round(cx_rel * 100)},{round(cy_rel * 100)}",   # % din cadru
            "de_minute": round((now - p["since"]) / 60),
            "eticheta_ro": "obiect nou" if p["kind"] == "nou" else "obiect disparut"}}
