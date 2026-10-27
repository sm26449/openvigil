#!/usr/bin/env python3
"""Urmarire cu model PARTAJAT + inferenta in LOT — nucleul nvr-ai.

Masurat: decodarea substream-ului (640x480) costa ~1ms, iar inferenta ~18ms — deci
bottleneck-ul NU e decodarea, ci (a) golirea buffer-ului RTSP prin grab() si (b)
serializarea a 5 model.track() pe un lock. De aceea:

  - cate un fir USOR de citire per camera (FrameReader): citeste stream-ul continuu
    si tine DOAR ultimul cadru (decupleaza cei 25 fps ai camerei de rata de procesare);
  - O SINGURA bucla (BatchLoop): ia ultimul cadru al fiecarei camere, face O inferenta
    in LOT (model.predict pe toate), apoi baga rezultatul in ByteTracker-ul fiecarei
    camere (stare usoara, postprocesare pe CPU, in afara lock-ului GPU).

Asa cei ~475ms/ciclu (2.1 fps) devin ~batch-predict (63ms/5) -> tinta de 4 fps atinsa
curat. Bookkeeping-ul (numarare, directie) e din track.py; peste el, analitica (linii,
zone, viteza). Evenimentele pleaca prin callback la nvr-web.
"""
import os
import threading
import time

import ai_analytics
import ai_scene
import detect
import scena
import scena_clasa
import settings
import track
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONF = track.CONF
FRAME_INTERVAL = track.FRAME_INTERVAL
RTSP = track.RTSP

_model = None
_init_lock = threading.Lock()
_predict_lock = threading.Lock()   # serializeaza predict-ul (bucla batch + detectia de clip)
_cam_trackers = {}                 # cam_id -> BYTETracker (starea camerei)
_clase = {}
_bt_args = None


def clip_detect(path, sample_fps=4, imgsz=640, conf=None):
    """Ruleaza modelul PARTAJAT (GPU) pe cadre esantionate dintr-un clip MP4 si intoarce
    chenare normalizate (0..1) per moment: {fps, frames:[{t, boxes:[{x,y,w,h,lbl,conf}]}]}.
    Extragem cadrele cu ffmpeg direct la sample_fps + scale 640 (decodare eficienta, doar
    cadrele necesare) — nu decodam tot clipul in Python. Serializat cu bucla de tracking
    prin _predict_lock (un scurt hopa in tracking la deschiderea unui clip)."""
    import glob
    import shutil
    import subprocess
    import tempfile

    import cv2
    import detect
    if conf is None:
        conf = detect.CONF          # acelasi prag ca detectia de incidente (0.50) — chenarele
    model = _get_model()            # de pe clip trebuie sa fie la fel de sigure ca incidentele,
    tmp = tempfile.mkdtemp(prefix="clipdet_")   # altfel arata ghiceli slabe (caine "person" 0.4)
    try:
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", path,
                        "-vf", f"fps={sample_fps},scale=640:-2", "-q:v", "3",
                        f"{tmp}/f_%04d.jpg"], capture_output=True, timeout=90)
        files = sorted(glob.glob(f"{tmp}/f_*.jpg"))
        frames, classes = [], list(_clase) or None
        for i in range(0, len(files), 16):
            imgs = [im for im in (cv2.imread(f) for f in files[i:i + 16]) if im is not None]
            if not imgs:
                continue
            with _predict_lock:
                results = model.predict(imgs, imgsz=imgsz, conf=conf,
                                        classes=classes, verbose=False)
            for j, res in enumerate(results):
                h, w = res.orig_shape
                boxes = []
                for b in res.boxes:
                    x1, y1, x2, y2 = b.xyxy[0].tolist()
                    boxes.append({"x": round(x1 / w, 4), "y": round(y1 / h, 4),
                                  "w": round((x2 - x1) / w, 4), "h": round((y2 - y1) / h, 4),
                                  "lbl": model.names.get(int(b.cls[0]), "?"),
                                  "conf": round(float(b.conf[0]), 2)})
                if any(bx["lbl"] == "person" for bx in boxes):   # a doua opinie pe person incert
                    try:
                        import verify
                        verify.refine(imgs[j], boxes)
                    except Exception as e:
                        logare.info(f"[verify] {str(e)[:70]}")
                frames.append({"t": round((i + j) / sample_fps, 2), "boxes": boxes})
        return {"fps": sample_fps, "frames": frames}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if imgsz > 640:
            # loturile mari (imgsz peste cel de productie) umfla cache-ul torch cu
            # activari de gigabytes care raman REZERVATE pana la trim-ul de 30 min —
            # intre timp NVDEC/NVENC raman fara memorie (traiit 2026-09-10: 183MB
            # liberi, clipurile picau cu "Cannot allocate memory"). Job unic -> trim.
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass


def _get_model():
    """Modelul partajat, incarcat o data (predict, fara track init). GPU daca e."""
    global _model, _clase
    with _init_lock:
        if _model is None:
            import numpy as np
            import torch
            from ultralytics import YOLO
            import detect
            m = YOLO(detect.MODEL_PATH)
            _clase = {i: n for i, n in m.names.items() if n in detect.INTERESTING}
            if not (bool(settings.get("track_gpu")) and torch.cuda.is_available()):
                m.to("cpu")
            m.predict(np.zeros((480, 640, 3), "uint8"), imgsz=640, verbose=False)  # warmup
            _model = m
            logare.info("[ai-track] model partajat incarcat")
    return _model


def _tracker_args():
    global _bt_args
    if _bt_args is None:
        import os
        import ultralytics
        import yaml
        from ultralytics.utils import IterableSimpleNamespace
        p = os.path.join(os.path.dirname(ultralytics.__file__),
                         "cfg", "trackers", "bytetrack.yaml")
        _bt_args = IterableSimpleNamespace(**yaml.safe_load(open(p)))
    return _bt_args


def _fresh_tracker():
    from ultralytics.trackers.byte_tracker import BYTETracker
    return BYTETracker(_tracker_args())


class CamTrack(track.Tracker):
    """Bookkeeping-ul unei camere (NU un fir — bucla batch il conduce). Peste baza:
    linii de numarare, zone de intrare, viteza, zabovire."""
    def __init__(self, cam_id, emit_fn):
        super().__init__(cam_id)
        self._emit_fn = emit_fn
        self._wh = None
        self.line_counts = {}

    def update(self, detections, now):
        if self._wh and (ai_analytics.lines(self.cam_id) or ai_analytics.zones(self.cam_id)):
            # det poate avea si box normalizat pe pozitia 5 (stationare) — ignorat aici
            for det in detections:
                tid, lbl, cx, cy = det[:4]
                t = self.tracks.get(tid)
                if not t:
                    continue
                old, new = (t["x"], t["y"]), (cx, cy)
                for nume, sens in ai_analytics.crossings(self.cam_id, old, new, self._wh):
                    self._on_crossing(lbl, nume, sens)
                for zona in ai_analytics.zone_entries(self.cam_id, old, new, self._wh):
                    self._emit_fn("intrare_zona", self.cam_id, {
                        "zona": zona, "eticheta": lbl,
                        "eticheta_ro": track.RO.get(lbl, lbl)})
        super().update(detections, now)

    def expire(self, now):
        # scheletul comun (LOST_TTL, MIN_FRAMES, stationare) vine din Tracker._expirate —
        # aici doar rezumatul bogat (viteza, zabovire); dublarea lui a ascuns odata
        # skip-ul de parcare (2026-09-09)
        gata = []
        for t in self._expirate(now):
            px_s = ai_analytics.speed_px_s(t)
            box_h = (t["nbox"][3] * self._wh[1]) if t.get("nbox") and self._wh else None
            gata.append({
                "eticheta": t["lbl"], "durata": round(t["last"] - t["t0"], 1),
                "inaltimi_s": ai_analytics.inaltimi_pe_s(px_s, box_h),
                "directie": track.directie(t["x"] - t["x0"], t["y"] - t["y0"]),
                "cadre": t["n"], "viteza": px_s, "cine": t.get("cine"),
                "kmh": ai_analytics.kmh(self.cam_id, px_s),
                "zabovire": (t["last"] - t["t0"]) >= ai_analytics.DWELL_SEC and px_s < 15})
        return gata

    def _on_crossing(self, lbl, nume, sens):
        c = self.line_counts.setdefault(nume, {"intra": 0, "iese": 0})
        c[sens] += 1
        self._emit_fn("trecere", self.cam_id, {
            "linie": nume, "sens": sens, "eticheta": lbl,
            "eticheta_ro": track.RO.get(lbl, lbl)})

    def _emit(self, obj):
        azi = time.strftime("%Y-%m-%d")
        if azi != self.count_day:
            self.counts, self.count_day = {}, azi
        self.counts[obj["eticheta"]] = self.counts.get(obj["eticheta"], 0) + 1
        date = {"eticheta": obj["eticheta"],
                "eticheta_ro": track.RO.get(obj["eticheta"], obj["eticheta"]),
                "durata": obj["durata"], "directie": obj["directie"],
                "cadre": obj["cadre"], "model": detect.amprenta()}
        if obj.get("viteza") is not None:
            date["viteza_px"] = obj["viteza"]
        if obj.get("kmh") is not None:
            date["kmh"] = obj["kmh"]
        if obj.get("inaltimi_s") is not None:
            date["inaltimi_s"] = obj["inaltimi_s"]
            if ai_analytics.este_biciclist(obj["eticheta"], obj["inaltimi_s"], obj["durata"]):
                date["biciclist"] = True
                date["eticheta_ro"] = "biciclist"
        if obj.get("zabovire"):
            date["zabovire"] = True
        if obj.get("cine"):
            date["cine"] = obj["cine"]
        try:
            self._emit_fn("obiect", self.cam_id, date)
        except Exception as e:
            logare.info(f"[ai-track] cam{self.cam_id} emit: {str(e)[:60]}")


class FrameReader(threading.Thread):
    """Citeste continuu stream-ul RTSP al unei camere si tine DOAR ultimul cadru.
    Decode ~1ms; decupleaza rata camerei (25 fps) de rata de procesare (4 fps)."""
    def __init__(self, cam_id):
        super().__init__(daemon=True, name=f"ai-read-cam{cam_id}")
        self.cam_id = cam_id
        self.stop_flag = False
        self._frame = None
        self._lock = threading.Lock()

    def latest(self):
        with self._lock:
            return self._frame

    def run(self):
        import cv2
        url = RTSP.format(cam_id=self.cam_id)
        while not self.stop_flag:
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                cap.release()
                time.sleep(10)
                continue
            while not self.stop_flag:
                ok, f = cap.read()
                if not ok:
                    break
                with self._lock:
                    self._frame = f
            cap.release()
            with self._lock:
                self._frame = None
            time.sleep(3)


class BatchLoop(threading.Thread):
    """O bucla: ia ultimul cadru al fiecarei camere, o inferenta in LOT, apoi fiecare
    camera prin ByteTracker-ul ei. Porneste/opreste cititori + trackere dupa track_cams."""
    def __init__(self, emit_fn):
        super().__init__(daemon=True, name="ai-batch")
        self.emit_fn = emit_fn
        self.stop_flag = False
        self.readers = {}              # cam_id -> FrameReader
        self.cams = {}                 # cam_id -> CamTrack
        self.scenes = {}               # cam_id -> SceneWatcher (obiect nou/disparut)
        self.schimbare = {}            # cam_id -> scena.Scena (detectie pe schimbare)
        self._hd = {}                  # cam_id -> (t, cadru HD) pentru decupajele scenei
        self._scena_scris = {}         # cam_id -> cand am scris ultima data harta pe disc

    def _sync(self, want):
        for c in list(self.readers):
            if c not in want:
                self.readers.pop(c).stop_flag = True
                self.cams.pop(c, None)
                _cam_trackers.pop(c, None)
        for c in want:
            if c not in self.readers or not self.readers[c].is_alive():
                r = FrameReader(c)
                self.readers[c] = r
                r.start()
            if c not in self.cams:
                self.cams[c] = CamTrack(c, self.emit_fn)

    def run(self):
        model = _get_model()
        logare.info("[ai-track] bucla batch pornita")
        while not self.stop_flag:
            t0 = time.time()
            ids = sorted(track.enabled_cams())
            self._sync(set(ids))
            # camere vegheate pt. obiect nou/disparut (subset al celor urmarite — de acolo cadrele)
            scene_cams = set(settings.get("scene_cams") or []) & set(ids)
            scene_persist = max(60, int(settings.get("scene_persist_min") or 30) * 60)
            scene_roi = float(settings.get("scene_roi_top") or 0.35)
            for c in list(self.scenes):
                if c not in scene_cams:
                    del self.scenes[c]
            schimbare_cams = {int(x) for x in (settings.get("scena_cams") or [])} & set(ids)
            for c in list(self.schimbare):
                if c not in schimbare_cams:
                    del self.schimbare[c]
            if not ids:
                time.sleep(1)
                continue
            frames = {c: self.readers[c].latest() for c in ids}
            now = time.time()
            valid = [(c, f) for c in ids if frames.get(c) is not None for f in [frames[c]]]
            for c in ids:                    # camerele fara cadru: inchide track-urile
                if frames.get(c) is None:
                    for obj in self.cams[c].expire(now):
                        self.cams[c]._emit(obj)
            if not valid:
                time.sleep(0.2)
                continue
            try:
                import metrici
                with metrici.cronometru("tracking"), _predict_lock:
                    # lock partajat cu clip_detect (nu doua predict simultan)
                    # half: FP16 pe GPU (pe placile Ada ~1,7x mai rapid, aceeasi precizie la
                    # detectie); ultralytics cade singur pe FP32 cand nu e CUDA
                    results = model.predict([f for _, f in valid], imgsz=640, conf=CONF,
                                            classes=list(_clase), verbose=False, quantize=_fp16())
            except Exception as e:
                logare.info(f"[ai-track] predict: {str(e)[:80]}")
                time.sleep(0.3)
                continue
            for (c, frame), res in zip(valid, results):
                ct = self.cams[c]
                ct._wh = (frame.shape[1], frame.shape[0])
                bt = _cam_trackers.setdefault(c, _fresh_tracker())
                try:
                    tracked = bt.update(res.boxes.cpu(), frame)   # ByteTracker pe CPU
                except Exception as e:
                    logare.info(f"[ai-track] cam{c} track: {str(e)[:70]}")
                    continue
                dets = []
                fw, fh = float(frame.shape[1]), float(frame.shape[0])
                for row in tracked:
                    if len(row) < 7:
                        continue
                    lbl = _clase.get(int(row[6]))   # x1,y1,x2,y2,id,conf,cls,idx
                    if not lbl:
                        continue
                    # float() -> Python (numpy float32 nu e serializabil in JSON la emit)
                    # + box normalizat pentru registrul de stationare (episoade E1)
                    nbox = (float(row[0]) / fw, float(row[1]) / fh,
                            float(row[2] - row[0]) / fw, float(row[3] - row[1]) / fh)
                    dets.append((int(row[4]), lbl, float(row[0] + row[2]) / 2,
                                 float(row[1] + row[3]) / 2, nbox))
                # o eroare pe o camera nu are voie sa omoare urmarirea TUTUROR:
                # exact asta s-a intamplat pe 2026-09-09 (un unpack gresit a ucis
                # tacut firul ai-batch pentru o zi intreaga, cu /health verde)
                try:
                    ct.update(dets, now)
                    ct.reid_sample(frame, now)
                    ct.frames += 1
                    for obj in ct.expire(now):
                        ct._emit(obj)
                except Exception as e:
                    logare.info(f"[ai-track] cam{c} update: {str(e)[:100]}")
                    continue
                # obiect nou/disparut: hranim watcher-ul cu cadrul + boxurile in miscare
                if c in scene_cams:
                    sw = self.scenes.get(c)
                    if sw is None or sw.persist_sec != scene_persist or sw.ignore_top != scene_roi:
                        sw = ai_scene.SceneWatcher(c, scene_persist, scene_roi)
                        self.scenes[c] = sw
                    try:
                        mv = res.boxes.xyxy.cpu().numpy() if len(res.boxes) else []
                        for ev in sw.feed(frame, mv, now):
                            self.emit_fn(ev["tip"], c, ev["date"])
                    except Exception as e:
                        logare.info(f"[ai-scene] cam{c}: {str(e)[:70]}")
                # detectie pe schimbare: fundal + regiuni noi, clasificate pe decupaj
                if c in schimbare_cams:
                    try:
                        self._schimbare(c, frame, now, model)
                    except Exception as e:
                        logare.info(f"[scena] cam{c}: {str(e)[:80]}")
            dt = time.time() - t0
            if dt < FRAME_INTERVAL:
                time.sleep(FRAME_INTERVAL - dt)
        for r in self.readers.values():
            r.stop_flag = True
        logare.info("[ai-track] bucla batch oprita")


def _fp16():
    """'fp16' pe GPU (ultralytics 8.4: `quantize`), None pe CPU."""
    try:
        import torch
        return "fp16" if torch.cuda.is_available() else None
    except Exception:
        return None


def _dir_scena(cam_id):
    d = os.path.join(ROOT, "data", "scena", str(int(cam_id)))
    os.makedirs(d, exist_ok=True)
    return d


def _scena_schimbare(self, c, frame, now, model):
    """Pasul de detectie pe schimbare al unei camere: hraneste motorul, clasifica
    candidatii maturi pe decupaj, emite `scena_candidat`, scrie harta pe disc la un minut."""
    import cv2
    sc = self.schimbare.get(c)
    if sc is None:
        sc = scena.Scena(c)
        self.schimbare[c] = sc
    maturi = sc.feed(frame, now)
    hd = None
    if maturi:
        # decupajul din fluxul principal: un om la 30 m are 15 px pe substream si 100+ pe HD;
        # cadrul HD se cere o data la 2 s per camera (go2rtc il decodeaza din fluxul deja deschis)
        t_hd, img_hd = self._hd.get(c, (0, None))
        if now - t_hd > 2:
            img_hd = detect._fetch_frame(c, "_main")
            self._hd[c] = (now, img_hd)
        hd = img_hd
    for cand in maturi:
        nbox = cand.nbox(sc.w, sc.h)
        crop = scena.decupaj(hd if hd is not None else frame, nbox)
        if crop is None:
            continue
        with _predict_lock:
            rez = scena_clasa.clasifica(crop, model, _clase, _fp16())
        cand.clasificat = rez
        cand.ultima_clasificare = now
        fn = f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(now))}-{cand.id}.jpg"
        cale = os.path.join(_dir_scena(c), fn)
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            with open(cale, "wb") as f:
                f.write(buf.tobytes())
        self.emit_fn("scena_candidat", c, {
            "candidat": cand.id, "eticheta": rez["eticheta"], "incredere": rez["incredere"],
            "sursa": rez["sursa"], "yolo": rez["yolo"], "vocab": rez["vocab"],
            "regiune": list(nbox), "cadre": cand.cadre, "durata": round(now - cand.t0, 1), "hd": hd is not None,
            "poza": fn if ok else None, "model": detect.amprenta()})
    if now - self._scena_scris.get(c, 0) >= 60:
        self._scena_scris[c] = now
        d = _dir_scena(c)
        bg = sc.fundal()
        if bg is not None:
            cv2.imwrite(os.path.join(d, "fundal.jpg"), bg, [cv2.IMWRITE_JPEG_QUALITY, 80])
        inst = sc.instabil()
        if inst is not None:
            cv2.imwrite(os.path.join(d, "instabil.png"), inst)
        try:
            import json
            with open(os.path.join(d, "stare.json"), "w") as f:
                json.dump({**sc.stare(), "t": now}, f)
        except OSError:
            pass


BatchLoop._schimbare = _scena_schimbare

_loop = None


def sync(emit_fn):
    """Porneste bucla batch daca nu ruleaza; ea urmareste singura track_cams."""
    global _loop
    if _loop is None or not _loop.is_alive():
        _loop = BatchLoop(emit_fn)
        _loop.start()


def snapshot():
    if _loop is None:
        return {}
    return {c: {"activ": _loop.is_alive(), "cadre": ct.frames, "azi": dict(ct.counts),
                "in_cadru": len(ct.tracks),
                "linii": {k: dict(v) for k, v in ct.line_counts.items()}}
            for c, ct in _loop.cams.items()}
