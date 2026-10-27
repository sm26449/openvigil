#!/usr/bin/env python3
"""Detectie pe schimbare: fundalul unei camere fixe si regiunile care difera de el.

O camera vede acelasi loc 90% din timp. Ce e in fundal (zid, copac, stalp) nu poate fi „ceva
nou”, oricat de vag arata noaptea pe infrarosu; ce difera de fundal e exact ce merita
clasificat — si clasificat la rezolutie intreaga, pe decupaj, nu pe tot cadrul la 640 px.

Motorul de aici e pur (numpy + OpenCV, fara torch) si TESTABIL cu cadre sintetice:
  - fundalul: MOG2 (amestec de gaussiene per pixel, cu umbre separate), invatat continuu;
    un obiect care RAMANE (masina parcata) intra singur in fundal dupa `istoric` cadre;
  - harta de instabilitate: cat de des se schimba fiecare pixel (frunze in vant, apa,
    steag); zonele instabile nu produc candidati — asa dispar „oamenii” din copaci;
  - schimbarile GLOBALE (apus, trecerea pe IR, farurile): cand prea mult din cadru s-a
    schimbat deodata e lumina, nu un obiect — fundalul se reinvata, candidatii se uita;
  - candidatii: regiuni de prim-plan urmarite de la cadru la cadru (potrivire IoU); unul
    devine „matur” — de clasificat — abia dupa `cadre_min` cadre consecutive SI o
    deplasare reala (un copac care tremura nu se deplaseaza);
  - clasificarea NU e aici: apelantul (ai_track) primeste candidatii maturi, decupeaza
    regiunea din cadru (ideal din fluxul HD) si intreaba modelele; rezultatul se scrie
    inapoi in candidat (`clasificat`), ca sa nu fie intrebat din nou prea des.
Costul: pe 480 px latime, ~2 ms per cadru pe CPU.
"""
import time

import cv2
import numpy as np

W = 480            # latimea de lucru (fundalul se invata pe imaginea mica; e de ajuns)

IMPLICIT = {
    "istoric": 1200,          # cadre de fundal (~5 min la 4 fps): cat de repede uita scena
    "prag": 25,               # varThreshold MOG2: sensibilitatea la schimbare
    "arie_min": 0.002,        # sub 0,2% din cadru = zgomot (ploaie, granulatie IR)
    "arie_max": 0.5,          # peste 50% = lumina, nu obiect
    "cadre_min": 4,           # cadre consecutive pana cand o regiune devine candidat matur
    "deplasare_min": 0.03,    # fractie din latime: un obiect real se misca; frunzele tremura pe loc
    "zgomot_regiuni": 25,     # peste atatea regiuni intr-un cadru = ploaie/zapada/granulatie: cadrul se sare
    "clasificari_pe_min": 8,  # cel mult atatea clasificari pe minut per camera (bugetul GPU)
    "instabil_prag": 0.35,    # pixelii in prim-plan peste 35% din timp sunt „instabili”
    "instabil_rata": 0.005,   # cat de repede se invata harta de instabilitate
    "reset_global": 0.4,      # peste 40% din cadru schimbat = lumina: reinvatam fundalul
    "reclasificare_sec": 10,  # cat de des intrebam din nou modelele despre acelasi candidat
    "disparut_sec": 2.0,      # fara prim-plan atata timp = candidatul s-a inchis
    "banda_sus": 0.08,        # fasiile cu OSD-ul camerei (ora sus, numele jos) se ignora
    "banda_jos": 0.08,
}


class Candidat:
    __slots__ = ("id", "box", "t0", "last", "cadre", "cx0", "cy0", "cx", "cy",
                 "clasificat", "ultima_clasificare", "arie0")

    def __init__(self, cid, box, now):
        self.id = cid
        self.box = box                     # (x, y, w, h) in pixeli de lucru
        self.t0 = self.last = now
        self.cadre = 1
        self.cx0 = self.cx = box[0] + box[2] / 2
        self.cy0 = self.cy = box[1] + box[3] / 2
        self.arie0 = box[2] * box[3]
        self.clasificat = None             # rezultatul modelelor, pus de apelant
        self.ultima_clasificare = 0.0

    def nbox(self, w, h):
        """Cutia normalizata (0..1) fata de cadrul de lucru."""
        x, y, bw, bh = self.box
        return (round(x / w, 4), round(y / h, 4), round(bw / w, 4), round(bh / h, 4))

    def deplasare(self):
        return ((self.cx - self.cx0) ** 2 + (self.cy - self.cy0) ** 2) ** 0.5

    def rezumat(self, w, h):
        return {"id": self.id, "nbox": self.nbox(w, h), "cadre": self.cadre,
                "durata": round(self.last - self.t0, 1),
                "deplasare": round(self.deplasare() / w, 3)}


def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    if inter <= 0:
        return 0.0
    return inter / (aw * ah + bw * bh - inter)


def _uneste(boxes, prag=0.1):
    """Cutiile care se suprapun (IoU > prag) devin una singura (uniunea lor)."""
    boxes = [list(b) for b in boxes]
    schimbat = True
    while schimbat:
        schimbat = False
        for i in range(len(boxes)):
            for j in range(i + 1, len(boxes)):
                if _iou(boxes[i], boxes[j]) > prag:
                    a, b = boxes[i], boxes[j]
                    x1, y1 = min(a[0], b[0]), min(a[1], b[1])
                    x2 = max(a[0] + a[2], b[0] + b[2])
                    y2 = max(a[1] + a[3], b[1] + b[3])
                    boxes[i] = [x1, y1, x2 - x1, y2 - y1]
                    del boxes[j]
                    schimbat = True
                    break
            if schimbat:
                break
    return [tuple(b) for b in boxes]


class Scena:
    """Fundalul unei camere + candidatii („ceva nou”) urmariti de la cadru la cadru."""

    def __init__(self, cam_id, cfg=None):
        self.cam_id = cam_id
        self.cfg = {**IMPLICIT, **(cfg or {})}
        self.w = self.h = 0
        self.cadre = 0
        self.resetari = 0
        self.ultimul_reset = 0.0
        self.candidati = {}
        self._next = 1
        self._instabil = None       # float32 (h, w): frecventa prim-planului per pixel
        self._kernel_o = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kernel_c = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        self._mog = self._nou_mog()
        self.ultimul_raport = 0.0   # fractia de cadru schimbata la ultimul cadru
        self.cadre_zgomot = 0       # cadre sarite ca „ploaie/zgomot”
        self._clasificari = []      # momentele ultimelor clasificari (bugetul pe minut)

    def _nou_mog(self):
        return cv2.createBackgroundSubtractorMOG2(history=int(self.cfg["istoric"]),
                                                  varThreshold=float(self.cfg["prag"]),
                                                  detectShadows=True)

    # ---- intrarea ----
    def _mic(self, frame):
        h, w = frame.shape[:2]
        if w != W:
            frame = cv2.resize(frame, (W, max(1, int(h * W / w))), interpolation=cv2.INTER_AREA)
        return frame

    def feed(self, frame_bgr, now=None):
        """Un cadru nou. Intoarce candidatii MATURI care cer (re)clasificare acum.
        Apelantul decupeaza regiunea (c.nbox) si pune rezultatul in c.clasificat,
        apoi c.ultima_clasificare = now."""
        now = now or time.time()
        cfg = self.cfg
        small = self._mic(frame_bgr)
        self.h, self.w = small.shape[:2]
        self.cadre += 1
        fg = self._mog.apply(small)
        fg = (fg == 255).astype(np.uint8)         # 127 = umbra: nu e obiect
        # OSD-ul camerei (ora, numele) se schimba in fiecare secunda: nu e „ceva nou”
        sus, jos = int(self.h * cfg["banda_sus"]), int(self.h * (1 - cfg["banda_jos"]))
        fg[:sus, :] = 0
        fg[jos:, :] = 0
        raport = float(fg.mean())
        self.ultimul_raport = raport
        if raport > cfg["reset_global"]:
            # lumina s-a schimbat peste tot: fundal nou, candidatii de pana acum nu mai conteaza
            self._mog = self._nou_mog()
            self.candidati.clear()
            self.resetari += 1
            self.ultimul_reset = now
            return []
        # harta de instabilitate: medie mobila a prim-planului per pixel
        if self._instabil is None or self._instabil.shape != fg.shape:
            self._instabil = np.zeros(fg.shape, np.float32)
        r = cfg["instabil_rata"]
        self._instabil *= (1 - r)
        self._instabil += fg * r
        fg[self._instabil > cfg["instabil_prag"]] = 0
        # curatare morfologica: zgomot afara, gaurile inchise
        fg = cv2.morphologyEx(fg * 255, cv2.MORPH_OPEN, self._kernel_o)
        fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, self._kernel_c)
        contururi, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        arie_cadru = self.w * self.h
        boxes = []
        for c in contururi:
            x, y, bw, bh = cv2.boundingRect(c)
            arie = bw * bh
            if arie < cfg["arie_min"] * arie_cadru or arie > cfg["arie_max"] * arie_cadru:
                continue
            boxes.append((x, y, bw, bh))
        if len(boxes) > cfg["zgomot_regiuni"]:
            # ploaie, zapada, granulatie de infrarosu: o puzderie de petice mici deodata.
            # Nu e „ceva nou”, iar candidatii n-ar fi decat picaturi: sarim cadrul.
            self.cadre_zgomot += 1
            return []
        boxes = _uneste(boxes)
        # potrivire cu candidatii existenti (IoU), cei mai mari intai
        vazuti = set()
        for b in sorted(boxes, key=lambda b: -(b[2] * b[3])):
            best, best_iou = None, 0.25
            for cid, c in self.candidati.items():
                if cid in vazuti:
                    continue
                v = _iou(c.box, b)
                if v > best_iou:
                    best, best_iou = cid, v
            if best is None:
                c = Candidat(self._next, b, now)
                self._next += 1
                self.candidati[c.id] = c
                vazuti.add(c.id)
            else:
                c = self.candidati[best]
                c.box = b
                c.cx, c.cy = b[0] + b[2] / 2, b[1] + b[3] / 2
                c.last = now
                c.cadre += 1
                vazuti.add(best)
        # candidatii nevazuti de ceva vreme s-au inchis (absorbiti in fundal sau plecati)
        for cid in [cid for cid, c in self.candidati.items() if now - c.last > cfg["disparut_sec"]]:
            del self.candidati[cid]
        # maturi: destule cadre SI deplasare reala (sau crestere clara), si nu prea des
        maturi = []
        dep_min = cfg["deplasare_min"] * self.w
        self._clasificari = [x for x in self._clasificari if now - x < 60]
        buget = int(cfg["clasificari_pe_min"]) - len(self._clasificari)
        for c in sorted(self.candidati.values(), key=lambda c: -(c.box[2] * c.box[3])):
            if buget <= 0:
                break
            if c.cadre < cfg["cadre_min"]:
                continue
            arie = c.box[2] * c.box[3]
            if c.deplasare() < dep_min and not (c.arie0 and arie > 1.5 * c.arie0):
                continue
            if c.clasificat is not None and now - c.ultima_clasificare < cfg["reclasificare_sec"]:
                continue
            maturi.append(c)
            buget -= 1
            self._clasificari.append(now)
        return maturi

    # ---- iesirea ----
    def fundal(self):
        """Imaginea fundalului invatat (BGR, latimea de lucru) sau None la inceput."""
        img = self._mog.getBackgroundImage()
        return img if img is not None and img.size else None

    def instabil(self):
        """Masca zonelor instabile (uint8 0/255), la latimea de lucru."""
        if self._instabil is None:
            return None
        return ((self._instabil > self.cfg["instabil_prag"]) * 255).astype(np.uint8)

    def stare(self):
        return {"cadre": self.cadre, "candidati": len(self.candidati), "cadre_zgomot": self.cadre_zgomot,
                "instabil_pct": round(float((self._instabil > self.cfg["instabil_prag"]).mean() * 100), 1)
                if self._instabil is not None else 0.0,
                "resetari": self.resetari, "ultimul_reset": self.ultimul_reset,
                "schimbat_pct": round(self.ultimul_raport * 100, 1)}


def decupaj(frame_bgr, nbox, margine=0.25, min_lat=96):
    """Regiunea `nbox` (normalizata) din cadrul dat, cu margine de context in jur si cel
    putin `min_lat` px pe latura (un obiect de 20 px nu se clasifica, dar cu context da)."""
    h, w = frame_bgr.shape[:2]
    x, y, bw, bh = nbox
    cx, cy = (x + bw / 2) * w, (y + bh / 2) * h
    pw, ph = max(bw * w * (1 + 2 * margine), min_lat), max(bh * h * (1 + 2 * margine), min_lat)
    x1, y1 = int(max(0, cx - pw / 2)), int(max(0, cy - ph / 2))
    x2, y2 = int(min(w, cx + pw / 2)), int(min(h, cy + ph / 2))
    if x2 <= x1 or y2 <= y1:
        return None
    return frame_bgr[y1:y2, x1:x2]
