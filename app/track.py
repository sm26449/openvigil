#!/usr/bin/env python3
"""Urmarirea obiectelor (ByteTrack) — numarare exacta, bucata cu bucata.

Detectia obisnuita vede "prezenta de masina" o data la 1,5s: doua masini una dupa
alta sunt un singur eveniment. Aici, pe camerele ALESE (strada), un lucrator citeste
substreamul RTSP la ~4 cadre/s si ruleaza YOLO cu ByteTrack: fiecare obiect primeste
un ID care traieste cat sta in cadru. Cand ID-ul dispare, obiectul devine eveniment
"obiect" in jurnal: eticheta, durata, directia de mers (din deplasarea neta — fara
linii de desenat) si numarul de cadre in care a fost vazut.

"Cate masini au trecut azi?" se numara apoi din aceste evenimente: exact, nu pe
episoade de prezenta. Costul: un decode de substream + ~4 inferente/s per camera
urmarita — de aceea e opt-in per camera (settings.track_cams), nu global.
"""
import threading
import time

import settings
import logare
import config as C

RO = {"person": "om", "car": "masina", "motorcycle": "motocicleta", "bus": "autobuz",
      "truck": "camion", "bird": "pasare", "cat": "pisica", "dog": "caine",
      "horse": "cal", "sheep": "oaie", "cow": "vaca", "bear": "urs",
      "chicken": "gaina", "pigeon": "porumbel"}

FRAME_INTERVAL = 0.25   # ~4 inferente/s — destul ca ByteTrack sa lege ID-urile
CONF = 0.5
MIN_FRAMES = 6          # un track confirmat = vazut in minim 6 cadre (~1,5s)
LOST_TTL = 2.5          # secunde fara ID -> obiectul a iesit din cadru
TRAIL_PAS = 2.0         # secunde intre punctele de traiectorie (episoade E3)
TRAIL_MAX = 150         # puncte pastrate; la umplere se injumatateste rezolutia
MIN_MOVE = 40           # px de deplasare neta sub care obiectul e "stationar"
RTSP = C.GO2RTC_RTSP + "/cam{cam_id}"

workers = {}


def enabled_cams():
    return {int(c) for c in (settings.get("track_cams") or [])}


def directie(dx, dy, min_move=MIN_MOVE):
    """Directia de mers din deplasarea neta, in coordonate de imagine."""
    if max(abs(dx), abs(dy)) < min_move:
        return "stationar"
    if abs(dx) >= abs(dy):
        return "stanga→dreapta" if dx > 0 else "dreapta→stanga"
    return "sus→jos" if dy > 0 else "jos→sus"


class Tracker(threading.Thread):
    def __init__(self, cam_id):
        super().__init__(daemon=True, name=f"track-cam{cam_id}")
        self.cam_id = cam_id
        self.stop_flag = False
        self.tracks = {}        # id -> stare
        self.counts = {}        # eticheta -> numarate azi
        self.count_day = time.strftime("%Y-%m-%d")
        self.frames = 0
        self._reid_busy = False   # un singur job de re-ID per camera (reid.sample)

    # ---- bookkeeping pur (testabil fara model/camera) ----
    def update(self, detections, now):
        """detections = [(track_id, label, cx, cy)] sau, cu al 5-lea element,
        [(track_id, label, cx, cy, box_normalizat)] — box-ul alimenteaza
        registrul de stationare (episoade E1)."""
        persoane = []      # centrele normalizate ale oamenilor -> episoade (E2)
        for det in detections:
            tid, lbl, cx, cy = det[:4]
            nbox = det[4] if len(det) > 4 else None
            t = self.tracks.get(tid)
            if t is None:
                t = self.tracks[tid] = {"lbl": lbl, "t0": now, "last": now,
                                        "x0": cx, "y0": cy, "x": cx, "y": cy, "n": 1}
            else:
                t.update(last=now, x=cx, y=cy)
                t["n"] += 1
            if nbox is not None:
                self._stationar(tid, t, nbox, now)
                t["nbox"] = nbox          # ultimul box: re-ID (persoane) + cautarea pe regiune
                if lbl == "person":
                    centru = (nbox[0] + nbox[2] / 2, nbox[1] + nbox[3] / 2)
                    if t["n"] >= MIN_FRAMES:
                        persoane.append(centru)
                    self._traseu(t, centru, now)
        if persoane:
            import episoade
            episoade.cadru(self.cam_id, persoane, now)

    def _traseu(self, t, centru, now):
        """Traiectoria esantionata a unui om (episoade E3: "ce a facut" + mini-harta).
        Un punct la ~TRAIL_PAS secunde; cand se umple, injumatatim rezolutia —
        FORMA drumului conteaza, nu fiecare pas."""
        if now - t.get("trail_t", 0.0) < TRAIL_PAS:
            return
        t["trail_t"] = now
        tr = t.setdefault("trail", [])
        tr.append((round(centru[0], 3), round(centru[1], 3)))
        if len(tr) > TRAIL_MAX:
            t["trail"] = tr[::2]

    def reid_sample(self, frame, now):
        """Esantioneaza crop-uri de corp pentru oamenii urmariti si le da prin
        OSNet (reid.sample, asincron: cadru HD + inferenta intr-un executor —
        bucla care ne cheama nu asteapta). Chemat din bucla reala, DUPA update()."""
        import reid
        reid.sample(self, frame, now)

    def _stationar(self, tid, t, nbox, now):
        """Ancora de stationare: cand centrul nu se urneste MOVE_EPS, dupa
        STATIONAR_MIN vehiculul devine parcare (registrul emite tranzitiile)."""
        import stationar
        if t["lbl"] not in stationar.VEHICULE:
            return
        ncx, ncy = nbox[0] + nbox[2] / 2, nbox[1] + nbox[3] / 2
        if "ax" not in t:
            t["ax"], t["ay"], t["at"] = ncx, ncy, now
            return
        if abs(ncx - t["ax"]) + abs(ncy - t["ay"]) > stationar.MOVE_EPS:
            t["ax"], t["ay"], t["at"] = ncx, ncy, now      # s-a urnit: ancora se muta
            if t.pop("stationar", None):
                stationar.plecare(self.cam_id, tid, now)   # pleaca din parcare
        elif "stationar" not in t and now - t["at"] >= stationar.STATIONAR_MIN:
            t["stationar"] = True
            stationar.sosire(self.cam_id, tid, t["lbl"], nbox, now, since=t["at"])
        elif t.get("stationar"):
            stationar.vazut(self.cam_id, tid, nbox, now)

    def _expirate(self, now):
        """Scheletul COMUN al expirarii (folosit si de CamTrack din ai_track, care
        isi construieste alt rezumat): inchide track-urile disparute si intoarce
        starile lor brute. Tot aici: curatenia stationarii + track-urile parcate
        NU se numara ca 'obiect' (episodul de parcare le acopera)."""
        import episoade
        import stationar
        stationar.tick(now)
        episoade.tick(self.cam_id, now)
        out = []
        for tid in list(self.tracks):
            t = self.tracks[tid]
            if now - t["last"] < LOST_TTL:
                continue
            del self.tracks[tid]
            if t.get("stationar"):
                continue        # episodul de parcare il acopera; nu-l numaram si ca obiect
            if t["n"] < MIN_FRAMES:
                continue        # aparitie de-un cadru-doua = zgomot, nu obiect
            if t["lbl"] == "person":
                episoade.track_inchis(self.cam_id,
                                      directie(t["x"] - t["x0"], t["y"] - t["y0"]), now,
                                      trail=t.get("trail"),
                                      durata=t["last"] - t["t0"],
                                      cine=t.get("cine"))
            out.append(t)
        return out

    def expire(self, now):
        """Inchide track-urile disparute; intoarce obiectele confirmate."""
        return [{"eticheta": t["lbl"],
                 "durata": round(t["last"] - t["t0"], 1),
                 "directie": directie(t["x"] - t["x0"], t["y"] - t["y0"]),
                 "cadre": t["n"], "cine": t.get("cine"),
                 "chenar": [round(v, 3) for v in t["nbox"]] if t.get("nbox") else None}
                for t in self._expirate(now)]

    def _emit(self, obj):
        azi = time.strftime("%Y-%m-%d")
        if azi != self.count_day:
            self.counts, self.count_day = {}, azi
        self.counts[obj["eticheta"]] = self.counts.get(obj["eticheta"], 0) + 1
        try:
            import hooks
            extra = {"cine": obj["cine"]} if obj.get("cine") else {}
            if obj.get("chenar"):
                extra["chenar"] = obj["chenar"]     # ultimul chenar: cautarea pe regiune
            hooks.emit("obiect", self.cam_id, eticheta=obj["eticheta"],
                       eticheta_ro=RO.get(obj["eticheta"], obj["eticheta"]),
                       durata=obj["durata"], directie=obj["directie"],
                       cadre=obj["cadre"], **extra)
        except Exception:
            pass

    # ---- bucla reala ----
    def run(self):
        import cv2
        import torch
        from ultralytics import YOLO
        import detect
        model = YOLO(detect.MODEL_PATH)   # instanta proprie: trackerul tine stare per model
        # clasele se iau din model, nu din COCO — merge si cu modelul fine-tuned
        clase = {i: n for i, n in model.names.items() if n in detect.INTERESTING}
        # implicit CPU: instanta proprie per lucrator nu incape pe GPU-ul de 16GB alaturi
        # de detectie + FaceID + LPR + SigLIP + Qwen (settings.track_gpu=True forteaza GPU)
        use_gpu = bool(settings.get("track_gpu")) and torch.cuda.is_available()
        device = 0 if use_gpu else "cpu"
        if not use_gpu:
            model.to("cpu")
        url = RTSP.format(cam_id=self.cam_id)
        logare.info(f"[track] cam{self.cam_id}: pornesc urmarirea ({url})")
        while not self.stop_flag:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                time.sleep(10)
                continue
            last = 0.0
            while not self.stop_flag:
                # grab() consuma cadrul din buffer FARA sa-l decodeze complet —
                # decodarea+conversia (partea scumpa) se face doar pentru cadrele
                # pe care chiar le inferam (4/s din ~15/s). Firul asta statea la
                # 38% dintr-un nucleu decodand degeaba 11 cadre pe secunda.
                if not cap.grab():
                    break
                now = time.time()
                if now - last < FRAME_INTERVAL:
                    continue
                last = now
                ok, frame = cap.retrieve()
                if not ok:
                    break
                try:
                    res = model.track(frame, imgsz=640, conf=CONF,
                                      classes=list(clase), persist=True,
                                      tracker="bytetrack.yaml", device=device,
                                      verbose=False)[0]
                except Exception as e:
                    logare.info(f"[track] cam{self.cam_id}: {str(e)[:80]}")
                    break
                dets = []
                fh, fw = frame.shape[:2]
                for b in res.boxes:
                    if b.id is None:
                        continue
                    lbl = clase.get(int(b.cls))
                    if not lbl:
                        continue
                    x1, y1, x2, y2 = b.xyxy[0].tolist()
                    nbox = (x1 / fw, y1 / fh, (x2 - x1) / fw, (y2 - y1) / fh)
                    dets.append((int(b.id), lbl, (x1 + x2) / 2, (y1 + y2) / 2, nbox))
                self.update(dets, now)
                self.reid_sample(frame, now)
                self.frames += 1
                for obj in self.expire(now):
                    self._emit(obj)
            cap.release()
            # fluxul a cazut (camera repornita, go2rtc reincarcat) — reluam
            for obj in self.expire(time.time() + LOST_TTL + 1):
                self._emit(obj)
            time.sleep(5)
        logare.info(f"[track] cam{self.cam_id}: oprit")


def sync():
    """Porneste/opreste lucratorii ca sa reflecte settings.track_cams.
    Cand ai_service e pornit, urmarirea o face nvr-ai — oprim lucratorii locali."""
    if settings.get("ai_service"):
        for cid in list(workers):
            workers.pop(cid).stop_flag = True
        return
    want = enabled_cams()
    for cid in list(workers):
        if cid not in want:
            workers.pop(cid).stop_flag = True
    for cid in want:
        if cid not in workers or not workers[cid].is_alive():
            w = Tracker(cid)
            workers[cid] = w
            w.start()


def snapshot():
    """Starea pe scurt pentru UI: {cam: {activ, cadre, azi: {eticheta: n}}}."""
    return {cid: {"activ": w.is_alive(), "cadre": w.frames,
                  "azi": dict(w.counts), "in_cadru": len(w.tracks)}
            for cid, w in workers.items()}
