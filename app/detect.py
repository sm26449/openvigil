#!/usr/bin/env python3
"""Detectie animale/persoane pe camerele selectate (YOLO pe GPU, cu fallback CPU).

Un thread per camera activata: ia un cadru JPEG din go2rtc (/api/frame.jpeg, substreamul
e deja la ~640px — exact marimea de inferenta) la fiecare ~1.5s si ruleaza modelul din
slotul activ data/yolo11n.pt (numele fisierului e istoric; din 2026-08-15 contine yolo11m).
Gainile si porumbeii apar amandoi ca "bird" — modelul standard nu distinge specii de pasari;
pentru asta ar trebui un model fine-tuned (pas separat).

Etichetele active + istoricul ajung in UI prin /api/events (cheia "detect"). Pornit/oprit
per camera din UI; persistat in data/detect.json. Inferentele sunt serializate cu un lock
(un singur model incarcat); la ~1 cadru/s per camera CPU-ul abia se simte.
"""
import collections
import json
import os
import threading
import time

import requests

import camctl
import config as C
import describe
import ai_client
import faces
import notify
import plates
import settings
import snaps
import stationar
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG_FILE = os.path.join(ROOT, "data", "detect.json")
EXT_FILE = os.path.join(ROOT, "data", "cameras.json")
MODEL_PATH = os.path.join(ROOT, "data", "yolo11n.pt")  # slotul activ (nume istoric; acum yolo11m)

INTERVAL = 1.5   # secunde intre cadre, per camera
CONF = 0.50      # pragul de incredere (0.40 dadea multe fals-pozitive "bird" pe yolo11n)

# Filtre impotriva fals-pozitivelor. Modelul mic (yolo11n) "vede" oameni in colturi de
# ziduri si tufe, mai ales noaptea pe infrarosu: 616 astfel de raportari intr-o singura zi.
# Filtrele de mai jos nu depind de scena, deci raman valabile si dupa repozitionarea camerelor.
EDGE_MARGIN = 0.02   # obiectele lipite de marginea cadrului sunt aproape mereu false
MIN_AREA = 0.0015    # sub 0.15% din cadru = zgomot (la 640x360 inseamna ~30x12 px)
MAX_AREA = 0.60      # peste 60% din cadru = de obicei o eroare de incadrare
CONFIRM = 2          # cate cadre consecutive trebuie sa contina obiectul ca sa-l raportam
ACTIVE_TTL = 10  # eticheta dispare din badge dupa atatea secunde fara re-detectie

# Etichetele care ne intereseaza, indiferent de model. Harta id->eticheta NU mai e
# hardcodata pe COCO: se construieste din model.names la incarcare, ca modelul
# fine-tuned cu clase proprii (chicken, pigeon) sa mearga fara nicio modificare.
INTERESTING = {"person", "car", "motorcycle", "bus", "truck", "bird", "cat", "dog",
               "horse", "sheep", "cow", "bear", "chicken", "pigeon"}
CLASSES = {}   # id -> eticheta, completata de _get_model() din modelul incarcat
# etichetele in romana, ca fluxurile de automatizare sa nu fie nevoite sa traduca ele
RO = {"person": "om", "car": "masina", "motorcycle": "motocicleta", "bus": "autobuz",
      "truck": "camion", "bird": "pasare", "cat": "pisica", "dog": "caine",
      "horse": "cal", "sheep": "oaie", "cow": "vaca", "bear": "urs",
      "chicken": "gaina", "pigeon": "porumbel"}
# Vehiculele se confirma dintr-un singur cadru: sunt obiecte mari (nu zgomot de 15px)
# si trec repede — cu doua cadre la 1,5s distanta, o masina in mers ar iesi din cadru
# inainte sa apucam sa-i citim numarul.
VEHICLES = {"car", "motorcycle", "bus", "truck"}
# Pasarile se confirma ca UN grup: modelul clipeste intre chicken/pigeon/bird la
# ~50-55% incredere de la cadru la cadru, deci pe etichete separate confirmarea
# de 2 cadre trecea aleator (sute de cadre-gunoi pe o camera, masurat 2026-09-09).
# Grupat, prezenta pasarii se confirma indiferent de aroma; eticheta raportata e
# cea din cadrul curent (cea mai sigura), pozele raman specifice.
BIRDS = {"bird", "pigeon", "chicken"}


def _confirm_key(lbl):
    """Cheia sub care o eticheta se confirma/imbatraneste: pasarile impart una."""
    return "pasare" if lbl in BIRDS else lbl


def _chenar(vazute, lbl):
    """Chenarul [x, y, w, h] (normalizat) cu increderea cea mai mare pentru eticheta,
    dintre cele trecute de filtre — pentru cautarea pe regiune (analiza.regiune)."""
    best = None
    for b in vazute or []:
        if b.get("lbl") == lbl and "filtru" not in b and (best is None or b["conf"] > best["conf"]):
            best = b
    return [best["x"], best["y"], best["w"], best["h"]] if best else None


def _moment():
    """'zi'/'noapte' din intervalul de noapte (Setari). Import lazy la rules ca sa
    evitam importul circular; None daca nu se poate calcula."""
    try:
        import rules
        return "noapte" if rules._is_night() else "zi"
    except Exception:
        return None


# cooldown intre declansarile automate de sirena (etichetele se aleg din tabul Setari)
AUTO_SIREN_COOLDOWN = 60
_auto_last = {}

# Face ID viu: cat timp un om NErecunoscut e in cadru, reincercam la fiecare 5s —
# omul intra cu spatele si se uita la camera abia dupa. Odata recunoscut, badge-ul
# se reimprospateaza cam la 10s (cat traieste eticheta activa).
FACE_RETRY = 5
FACE_OK_TTL = 10


def face_retry_due(person_active, now, last_try, last_ok,
                   retry=FACE_RETRY, ok_ttl=FACE_OK_TTL):
    """Pur si testabil: e momentul sa (re)incercam recunoasterea fetei?"""
    if not person_active:
        return False
    if now - last_try < retry:
        return False
    return now - last_ok >= ok_ttl

_model = None
_device = None
_lock = threading.Lock()  # incarcarea modelului + serializarea inferentelor


def _get_model():
    import ai_client
    ai_client.local_permis("YOLO")
    global _model, _device
    if _model is None:
        import torch
        from ultralytics import YOLO  # import lent (~5s) -> abia la prima folosire
        _device = 0 if torch.cuda.is_available() else "cpu"
        _model = YOLO(MODEL_PATH)
        CLASSES.clear()
        CLASSES.update({i: n for i, n in _model.names.items() if n in INTERESTING})
        logare.info(f"[detect] yolo pe {'GPU' if _device == 0 else 'CPU'}, "
              f"{len(CLASSES)} clase urmarite din {len(_model.names)}")
    return _model


def chenare_json(res):
    """Chenarele unui rezultat YOLO ca lista serializabila, normalizata [0..1] — sidecar-ul
    pozei salvate (snaps.save_frame), din care „gaseste-l pe toate camerele” taie omul."""
    out = []
    try:
        for b in res.boxes:
            x1, y1, x2, y2 = b.xyxyn[0].tolist()
            out.append({"cls": res.names[int(b.cls)], "x": round(x1, 4), "y": round(y1, 4),
                        "w": round(x2 - x1, 4), "h": round(y2 - y1, 4), "conf": round(float(b.conf), 3)})
    except Exception:
        pass
    return out


def persoane(img, conf=None, imgsz=640):
    """Box-urile 'person' (xyxy, px) dintr-un cadru, pe modelul partajat —
    pentru ancorarea re-ID din cadrul fetei (reid._ancora_din_cadru) si pentru
    „gaseste-l pe toate camerele” (conf mai mic: poza a fost deja o detectie). Rar,
    serializat cu restul inferentelor prin _lock."""
    with _lock:
        model = _get_model()     # in lock, ca la ceilalti apelanti (incarcare + predict)
        pid = [i for i, n in CLASSES.items() if n == "person"]
        res = model.predict(img, imgsz=imgsz, conf=conf or CONF, classes=pid,
                            device=_device, verbose=False)[0]
    return [b.xyxy[0].tolist() for b in res.boxes]


def reload_model():
    """Dupa inlocuirea fisierului model (fine-tune aplicat din UI): urmatoarea inferenta
    incarca noul model, fara restart de serviciu."""
    global _model
    with _lock:
        _model = None


_amprenta = {"cheie": None, "val": None}


def amprenta():
    """"Ce model a vazut asta?" — numele + SHA-256 scurt al fisierului de model,
    atasat evenimentelor de detectie (fingerprinting: proba mai solida in dosare,
    iar dupa un fine-tune aplicat evenimentele poarta VIZIBIL hash-ul nou).
    Hash-ul complet e in /capabilities (nvr-ai); cache pe (mtime, marime)."""
    try:
        st = os.stat(MODEL_PATH)
    except OSError:
        return None
    cheie = (st.st_mtime, st.st_size)
    if _amprenta["cheie"] != cheie:
        import hashlib
        h = hashlib.sha256()
        with open(MODEL_PATH, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _amprenta.update(cheie=cheie,
                         val=f"{os.path.basename(MODEL_PATH)}@{h.hexdigest()[:12]}")
    return _amprenta["val"]


# clasele care conteaza pt. un intrus la perimetru: oameni + vehicule (nu animale/pomi)
INTRUS_CLASE = {"person", "car", "truck", "bus", "motorcycle"}


def _run_confirm(img, imgsz, cam_id=None):
    """YOLO pe un cadru -> (dict {eticheta: incredere pt. clasele de intrus}, adnotat).

    Trece detectiile 'person' printr-o a doua opinie (verify): la lumina slaba curte-v1
    confunda caini/vehicule cu oameni, iar un 'person' fals ar declansa un intrus fals.
    Daca modelul stock dezminte, eticheta se corecteaza inainte de a decide intrusul."""
    import metrici
    with metrici.cronometru("confirmare"), _lock:
        res = _get_model().predict(img, imgsz=imgsz, conf=CONF, classes=list(CLASSES),
                                   device=_device, verbose=False)[0]
    h, w = res.orig_shape
    boxes = []
    for b in res.boxes:
        lbl = CLASSES.get(int(b.cls))
        if not lbl:
            continue
        x1, y1, x2, y2 = b.xyxy[0].tolist()
        boxes.append({"x": x1 / w, "y": y1 / h, "w": (x2 - x1) / w, "h": (y2 - y1) / h,
                      "lbl": lbl, "conf": round(float(b.conf), 2)})
    if any(bx["lbl"] == "person" for bx in boxes):
        try:
            import verify
            verify.refine(img, boxes)
        except Exception as e:
            logare.info(f"[detect] verify: {str(e)[:70]}")
    found = {}
    for bx in boxes:
        if bx["lbl"] not in INTRUS_CLASE:
            continue
        # vehiculul PARCAT (registrul de stationare) nu e un intrus nou — el a
        # declansat "intrus masina" la fiecare miscare din cadru, toata noaptea
        if cam_id is not None and bx["lbl"] in stationar.VEHICULE and \
                stationar.suprapune(cam_id, (bx["x"], bx["y"], bx["w"], bx["h"])):
            continue
        found[bx["lbl"]] = max(found.get(bx["lbl"], 0.0), bx["conf"])
    return found, (res.plot() if found else None)


def confirm_alarm(cam_id, imgsz=1280):
    """La o alarma de perimetru a camerei (linie/intrare), ruleaza YOLO ca sa confirme CE
    a declansat-o. Intoarce (dict {eticheta: incredere}, cadru_adnotat_sau_None, cadru).

    Doua incercari, ca sa prindem si timing-ul si rezolutia:
    1) cadrul SUBSTREAM cald (instant, din go2rtc) — un om/masina care trece la perimetru e
       inca in cadru; cadrul HD (ISAPI mainstream) dureaza 2-3s pana se negociaza, timp in
       care subiectul in miscare a iesit deja (de-aici veneau 0 confirmari noaptea).
    2) daca substreamul nu gaseste nimic, cadrul HD (rezolutie nativa 4-8MP + imgsz mare
       prinde obiectele mici la distanta pe campurile intinse; astea sunt statice -> delay OK)."""
    cam = _ext_cam(cam_id)
    if not cam:
        return {}, None, None
    img = _fetch_frame(cam_id)                       # 1) substream cald (instant)
    if img is not None:
        found, plot = _run_confirm(img, imgsz, cam_id)
        if found:
            return found, plot, img
    hd = faces.hd_frame(cam)                          # 2) fallback HD (rezolutie)
    if hd is not None:
        found, plot = _run_confirm(hd, imgsz, cam_id)
        if found or img is None:
            return found, plot, hd
    return {}, None, img


from concurrent.futures import ThreadPoolExecutor

_fetch_pool = None    # descarcatul cadrelor: I/O pur, elibereaza GIL, merge in paralel
_heavy_pool = None    # joburile grele per-camera (fete, placute) — nu blocheaza lotul


def _pools():
    global _fetch_pool, _heavy_pool
    if _fetch_pool is None:
        _fetch_pool = ThreadPoolExecutor(max_workers=12, thread_name_prefix="detect-fetch")
        _heavy_pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="detect-heavy")
    return _fetch_pool, _heavy_pool


# ---- motion-gating: nu trecem prin YOLO o scena care nu s-a schimbat ----
# Toate NVR-urile serioase (Viseron, Frigate, lightNVR...) ruleaza detectorul greu
# doar cand un frame-differencing ieftin vede miscare. Castigul la noi nu e viteza
# (GPU-ul e la 7%), ci reducerea fals-pozitivelor de zgomot si headroom la scalare:
# o scena statica nu mai trece prin model, deci nu mai poate inventa nimic din zgomot IR.
MOTION_DOWNSCALE = 8    # cat micsoram inainte de diff (ieftin + ignora zgomot fin)
MOTION_PIX_DELTA = 18   # diferenta de intensitate per pixel peste care il numaram "schimbat"
MOTION_THRESH = 0.003   # fractia de pixeli schimbati de la care e "miscare" (0.3%)
MOTION_HEARTBEAT = 8    # secunde: chiar fara miscare, inferam periodic (obiecte stationare
                        # + confirmare ca nu e nimic); sub ACTIVE_TTL, ca badge-ul sa nu clipeasca
TILE_COOLDOWN = 3       # secunde intre doua crop-uri HD pe aceeasi camera (HD e scump)
_gate_stats = {"total": 0, "gated": 0}   # cate camere am sarit vs total (observabilitate)


def _gray_small(img):
    import cv2
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = g.shape
    small = cv2.resize(g, (max(1, w // MOTION_DOWNSCALE), max(1, h // MOTION_DOWNSCALE)))
    return cv2.GaussianBlur(small, (3, 3), 0)


def has_motion(prev, cur, thresh=MOTION_THRESH, pix_delta=MOTION_PIX_DELTA):
    """Pur si testabil: fractia de pixeli schimbati intre doua cadre gri-mici depaseste
    pragul? Fara referinta (prima data / dimensiuni schimbate) -> True, ca sa nu ratam."""
    import numpy as np
    if prev is None or cur is None or prev.shape != cur.shape:
        return True
    diff = np.abs(cur.astype(np.int16) - prev.astype(np.int16))
    return np.count_nonzero(diff > pix_delta) / diff.size >= thresh


# ---- detectie pe regiuni: obiectele mici la distanta ----
# Pe substreamul de 640px un om la capatul strazii are ~20px si cade sub prag. Solutia
# (lightNVR "tiled", open-nvr "regiuni marite"): gasim UNDE s-a miscat ceva (din aceeasi
# harta de diff ca motion-gate), decupam regiunea din cadrul HD (4-8MP, unde omul chiar
# are pixeli) si inferam pe crop. Opt-in per camera — are sens la strada, nu peste tot.
TILE_MARGIN = 0.25     # cat context in jurul regiunii de miscare (fractie din latura ei)
TILE_MIN_PX = 96       # crop mai mic de atat nu merita o cerere HD
TILE_MAX_AREA = 0.08   # raportam din tiling doar obiectele MICI in cadrul intreg (<8%);
                       # cele mari le prinde oricum detectia normala pe substream
TILE_CONF = 0.55       # prag mai strict decat detectia normala (0.50): crop-ul marit
                       # poate genera fantome, iar aici nu avem confirmare pe 2 cadre


def motion_region(prev, cur, pix_delta=MOTION_PIX_DELTA):
    """Bounding-box-ul zonei care s-a schimbat, normalizat 0..1, sau None daca nimic.
    Se calculeaza pe harta gri-mica (aceeasi ca has_motion), deci practic gratis."""
    import numpy as np
    if prev is None or cur is None or prev.shape != cur.shape:
        return None
    mask = np.abs(cur.astype(np.int16) - prev.astype(np.int16)) > pix_delta
    ys, xs = np.where(mask)
    if xs.size < 3:
        return None
    h, w = mask.shape
    return (xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h)


def expand_region(region, margin=TILE_MARGIN):
    """Adauga margine unei regiuni normalizate, cu clamp la [0,1]."""
    x1, y1, x2, y2 = region
    mx, my = (x2 - x1) * margin, (y2 - y1) * margin
    return (max(0.0, x1 - mx), max(0.0, y1 - my),
            min(1.0, x2 + mx), min(1.0, y2 + my))


def _fetch_frame(cam_id):
    import cv2
    import numpy as np
    try:
        r = requests.get(f"{C.GO2RTC_API}/api/frame.jpeg",
                         params={"src": f"cam{cam_id}"}, timeout=8)
        if r.ok and r.content:
            return cv2.imdecode(np.frombuffer(r.content, np.uint8), cv2.IMREAD_COLOR)
    except requests.RequestException:
        pass
    return None


def _jpeg(img, quality=90):
    """Cadru BGR -> JPEG (pentru joburile trimise la nvr-ai ca fallback-frame)."""
    import cv2
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return buf.tobytes() if ok else None


class CamState:
    """Starea de detectie a UNEI camere. Nu mai e thread: cadrele vin din bucla
    comuna de lot (BatchLoop), aici traieste doar logica per-camera — filtre,
    confirmare, actiuni la aparitii. Cu 18 fire care chemau fiecare predict()
    pe cate un cadru, tot procesul statea la ~76% dintr-un nucleu numai din
    overhead Python serializat de GIL; lotul face UN predict pentru toate."""

    def __init__(self, cam_id):
        self.cam_id = cam_id
        self.active = {}                            # eticheta -> ultima detectie (epoch)
        self.recent = collections.deque(maxlen=50)  # tranzitii nou-aparute
        self._pending = {}                          # eticheta -> cadre consecutive vazute
        self._face_try = 0.0                        # ultima incercare de Face ID
        self._face_ok = 0.0                         # ultima recunoastere reusita
        self._face_busy = False                     # un singur job de fete o data
        self._prev_gray = None                      # cadrul anterior (gri-mic) pt. motion-gate
        self._last_infer = 0.0                      # ultima inferenta reala (heartbeat)
        self._tile_try = 0.0                        # ultimul crop HD (cooldown, HD e scump)
        self._tile_busy = False                     # un singur job de regiuni o data
        self.last_boxes = {"t": 0.0, "boxes": []}   # ultimele chenare (overlay, faza 6)

    def process(self, img, res, now):
        """Un cadru + rezultatul lui din lot -> toata logica per-camera."""
        import cv2
        zone = cam_cfg(self.cam_id).get("zone")
        h, w = img.shape[:2]
        hits = set()
        vazute = []      # TOATE chenarele + de ce au fost aruncate — overlay-ul (faza 6)
                         # arata exact ce vede modelul si ce inghit filtrele
        for b in res.boxes:
            lbl = CLASSES.get(int(b.cls))
            if not lbl:
                continue
            x1, y1, x2, y2 = b.xyxy[0].tolist()
            nx1, ny1, nx2, ny2 = x1 / w, y1 / h, x2 / w, y2 / h
            area = (nx2 - nx1) * (ny2 - ny1)
            box = {"x": round(nx1, 4), "y": round(ny1, 4), "w": round(nx2 - nx1, 4),
                   "h": round(ny2 - ny1, 4), "lbl": lbl, "conf": round(float(b.conf), 2)}
            vazute.append(box)
            if area < MIN_AREA or area > MAX_AREA:
                box["filtru"] = "marime"
                continue
            # lipit de marginea cadrului = obiect taiat sau artefact
            if (nx1 < EDGE_MARGIN or ny1 < EDGE_MARGIN
                    or nx2 > 1 - EDGE_MARGIN or ny2 > 1 - EDGE_MARGIN):
                box["filtru"] = "margine"
                continue
            if zone and not _in_zone(zone, (nx1 + nx2) / 2, (ny1 + ny2) / 2):
                box["filtru"] = "zona"
                continue
            # masina PARCATA nu se re-raporteaza cat sta pe loc (episoade E1):
            # tranzitiile (sosire/plecare) vin din registrul de stationare.
            # refresh: cat o vedem aici, parcarea e VIE — nu depindem de track
            if lbl in stationar.VEHICULE and stationar.suprapune(
                    self.cam_id, (nx1, ny1, nx2 - nx1, ny2 - ny1), refresh=True):
                box["filtru"] = "stationar"
                continue
            hits.add(lbl)
        # confirmare temporala pe CHEI (pasarile impart una — vezi BIRDS): raportam
        # abia dupa CONFIRM cadre la rand; eticheta raportata e cea mai sigura din
        # cadrul curent pentru cheia respectiva.
        best = {}                       # cheie -> (conf, eticheta reprezentativa)
        for box in vazute:
            if "filtru" in box:
                continue
            k = _confirm_key(box["lbl"])
            if box["conf"] > best.get(k, (0.0, ""))[0]:
                best[k] = (box["conf"], box["lbl"])
        chei = set(best)
        for k in list(self._pending):
            if k not in chei:
                del self._pending[k]
        confirmed = set()
        for k in chei:
            self._pending[k] = self._pending.get(k, 0) + 1
            if self._pending[k] >= (1 if k in VEHICLES else CONFIRM):
                confirmed.add(k)
        for box in vazute:   # trecut de filtre, dar inca neconfirmat pe 2 cadre
            if "filtru" not in box and _confirm_key(box["lbl"]) not in confirmed:
                box["filtru"] = "neconfirmat"
        self.last_boxes = {"t": now, "boxes": vazute}
        hits = {best[k][1] for k in confirmed}
        fresh = []
        for lbl in hits:
            # o pasare care isi schimba aroma (pigeon->chicken) NU e o aparitie noua:
            # prospetimea se judeca pe tot grupul, dar badge-ul/poza raman specifice
            aliasuri = BIRDS if lbl in BIRDS else {lbl}
            if not any(now - self.active.get(a, 0) <= ACTIVE_TTL for a in aliasuri):
                fresh.append(lbl)
                self.recent.appendleft(
                    {"type": lbl, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
            self.active[lbl] = now
        if fresh:
            snaps.save_dataset(self.cam_id, img)  # cadrul curat, pt. antrenament
            annotated = res.plot()  # cadrul cu chenarele desenate
            ok, buf = cv2.imencode(".jpg", annotated,
                                   [cv2.IMWRITE_JPEG_QUALITY, 85])
            jpeg = buf.tobytes() if ok else None
            moment = _moment()   # zi/noapte, atasat oricarei detectii pentru filtrare
            chenare_sc = chenare_json(res)
            for lbl in fresh:
                snaps.save_frame(self.cam_id, lbl, annotated, boxes=chenare_sc)
                notify.detection(self.cam_id, lbl, jpeg)
                try:
                    import hooks
                    # chenarul cel mai sigur al etichetei (normalizat): cautarea pe
                    # regiune din Inregistrari il compara cu dreptunghiul desenat
                    hooks.emit("detectie", self.cam_id, eticheta=lbl,
                               eticheta_ro=RO.get(lbl, lbl), moment=moment,
                               model=amprenta(), chenar=_chenar(vazute, lbl))
                except Exception:
                    pass
            self._maybe_siren(fresh)
            # semnalmente: descrierea persoanei (VLM) — in nvr-ai cand serviciul e viu
            # (modelul Qwen sta acolo), altfel local; isi face singura thread
            if "person" in fresh and describe.enabled():
                pboxes = [b.xyxy[0].tolist() for b in res.boxes
                          if CLASSES.get(int(b.cls)) == "person"]
                if not (ai_client.remote()
                        and ai_client.describe_person(self.cam_id, _jpeg(img), pboxes)):
                    threading.Thread(target=describe.process,
                                     args=(self.cam_id, img.copy(), pboxes),
                                     daemon=True).start()
            # vehicule: LPR (numar) + semnalmente VLM (culoare + tip: masina/gunoi/autobuz)
            if set(fresh) & VEHICLES:
                if plates.enabled():
                    # job greu (cadru HD + OCR) -> executorul separat, lotul nu asteapta
                    _pools()[1].submit(self._lpr_job, img.copy())
                if describe.enabled():
                    vboxes = [b.xyxy[0].tolist() for b in res.boxes
                              if CLASSES.get(int(b.cls)) in VEHICLES]
                    if not (ai_client.remote()
                            and ai_client.describe_vehicle(self.cam_id, _jpeg(img),
                                                           vboxes, moment)):
                        threading.Thread(target=describe.process_vehicle,
                                         args=(self.cam_id, img.copy(), vboxes, moment),
                                         daemon=True).start()
        # Face ID viu: la intrare SI cat timp omul ramane nerecunoscut in cadru —
        # reincercare la 5s; job greu (cadru HD + inferenta) -> executorul separat
        if (faces.enabled() and not self._face_busy
                and face_retry_due("person" in hits, now,
                                   self._face_try, self._face_ok)):
            self._face_try = now
            self._face_busy = True
            _pools()[1].submit(self._face_job, img.copy())

    def _tile_job(self, region):
        """Decupeaza regiunea de miscare din cadrul HD si infereaza pe ea — prinde
        obiectele mici la distanta. Autonom (nu prin confirmarea de 2 cadre): se
        declanseaza deja pe miscare gated + regiune, iar pragul e mai strict."""
        import cv2
        try:
            cam = _ext_cam(self.cam_id)
            img = faces.hd_frame(cam) if cam else None
            if img is None:
                return
            H, W = img.shape[:2]
            x1, y1, x2, y2 = expand_region(region)
            px1, py1, px2, py2 = int(x1 * W), int(y1 * H), int(x2 * W), int(y2 * H)
            if min(px2 - px1, py2 - py1) < TILE_MIN_PX:
                return
            crop = img[py1:py2, px1:px2]
            import metrici
            with metrici.cronometru("tiling"), _lock:
                res = _get_model().predict(crop, imgsz=640, conf=TILE_CONF,
                                           classes=list(CLASSES), device=_device,
                                           verbose=False)[0]
            now = time.time()
            gasit = []
            chenare = {}     # eticheta -> chenarul ei in CADRUL INTREG (pt. cautarea pe regiune)
            for b in res.boxes:
                lbl = CLASSES.get(int(b.cls))
                if not lbl:
                    continue
                bx1, by1, bx2, by2 = b.xyxy[0].tolist()
                # aria in CADRUL INTREG (nu in crop): pastram doar obiectele mici,
                # cele mari sunt deja prinse de detectia normala pe substream
                area = ((bx2 - bx1) / W) * ((by2 - by1) / H)
                if area > TILE_MAX_AREA:
                    continue
                gasit.append(lbl)
                if lbl not in chenare or float(b.conf) > chenare[lbl][4]:
                    chenare[lbl] = [round((px1 + bx1) / W, 3), round((py1 + by1) / H, 3),
                                    round((bx2 - bx1) / W, 3), round((by2 - by1) / H, 3),
                                    float(b.conf)]
            chenare = {k: v[:4] for k, v in chenare.items()}
            for lbl in set(gasit):
                if lbl not in self.active or now - self.active[lbl] > ACTIVE_TTL:
                    self.recent.appendleft(
                        {"type": lbl, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
                    annotated = res.plot()   # crop-ul HD cu chenarul — poza clara
                    snaps.save_frame(self.cam_id, lbl, annotated, boxes=chenare_json(res))
                    ok, buf = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
                    notify.detection(self.cam_id, lbl, buf.tobytes() if ok else None)
                    try:
                        import hooks
                        hooks.emit("detectie", self.cam_id, eticheta=lbl,
                                   eticheta_ro=RO.get(lbl, lbl), sursa="regiune",
                                   model=amprenta(), chenar=chenare.get(lbl))
                    except Exception:
                        pass
                self.active[lbl] = now
            if gasit:
                logare.info(f"[detect] cam{self.cam_id}: regiune -> {sorted(set(gasit))}")
        except Exception as e:
            logare.info(f"[detect] cam{self.cam_id}: tiling: {str(e)[:80]}")
        finally:
            self._tile_busy = False

    def _face_job(self, img):
        try:
            # modelul Face ID sta in nvr-ai (#9 pas 1); la esec de transport, fallback
            # local (faces se incarca lazy in web doar atunci)
            gasiti = ai_client.face_process(self.cam_id, _jpeg(img)) \
                if ai_client.remote() else None
            if gasiti is None:
                gasiti = faces.process(self.cam_id, img, _ext_cam(self.cam_id))
            now = time.time()
            if gasiti:
                self._face_ok = now
            for nume in gasiti:
                self.active[nume] = now
                self.recent.appendleft(
                    {"type": nume, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
        except Exception as e:
            logare.info(f"[detect] cam{self.cam_id}: face-id: {str(e)[:80]}")
        finally:
            self._face_busy = False

    def _lpr_job(self, img):
        try:
            numere = ai_client.lpr_process(self.cam_id, _jpeg(img)) \
                if ai_client.remote() else None
            if numere is None:
                numere = plates.process(self.cam_id, img, _ext_cam(self.cam_id))
            for numar in numere:
                self.active[numar] = time.time()
                self.recent.appendleft(
                    {"type": numar, "time": time.strftime("%Y-%m-%dT%H:%M:%S")})
        except Exception as e:
            logare.info(f"[detect] cam{self.cam_id}: lpr: {str(e)[:80]}")

    def _maybe_siren(self, fresh):
        """Sirena automata: eticheta aleasa in Setari + automatizarea pornita pe camera."""
        if not (set(fresh) & set(settings.get("auto_siren_labels"))):
            return
        if not cam_cfg(self.cam_id).get("auto_siren"):
            return
        now = time.time()
        if now - _auto_last.get(self.cam_id, 0) < AUTO_SIREN_COOLDOWN:
            return
        _auto_last[self.cam_id] = now
        cam = _ext_cam(self.cam_id)
        if not cam or not cam.get("features", {}).get("siren"):
            return
        try:
            snd = str(settings.get("auto_siren_sound") or 1)
            custom = snd.startswith("c")
            import drivers
            drivers.trigger_siren(cam, audio_id=int(snd.lstrip("c")),
                                  volume=settings.get("auto_siren_volume"),
                                  times=settings.get("auto_siren_times"), custom=custom)
            logare.info(f"[detect] cam{self.cam_id}: anunt automat {snd} (detectie: {fresh})")
            try:
                import hooks
                hooks.emit("sirena", self.cam_id, motiv=list(fresh), automat=True)
            except Exception:
                pass
        except requests.RequestException as e:
            logare.error(f"[detect] cam{self.cam_id}: sirena automata esuata: {e}")


class BatchLoop(threading.Thread):
    """Bucla comuna: cadrele tuturor camerelor se descarca in paralel, apoi UN
    predict pe tot lotul, apoi rezultatele merg la starile per-camera."""

    def __init__(self):
        super().__init__(daemon=True, name="detect-batch")
        self.stop_flag = False

    def run(self):
        while not self.stop_flag:
            t0 = time.time()
            try:
                ids = sorted(detectors)
                if ids:
                    fetch, _ = _pools()
                    frames = list(fetch.map(_fetch_frame, ids))
                    now = time.time()
                    gate_on = settings.get("motion_gate")
                    tile_cams = {int(c) for c in (settings.get("tile_cams") or [])}
                    # motion-gate: pastram in lot doar camerele cu miscare (sau care n-au
                    # fost inferate de HEARTBEAT secunde). Cadrul gri-mic se retine oricum,
                    # ca sa avem referinta pentru diff la iteratia urmatoare.
                    lot = []
                    for cid, img in zip(ids, frames):
                        if img is None:
                            continue
                        st = detectors.get(cid)
                        if st is None:
                            continue
                        prev = st._prev_gray
                        gray = _gray_small(img)
                        misca = has_motion(prev, gray)
                        st._prev_gray = gray
                        # detectie pe regiuni: pe camerele alese, cand e miscare, decupam
                        # regiunea din HD si o inferam separat (in heavy_pool)
                        if (misca and cid in tile_cams and not st._tile_busy
                                and now - st._tile_try > TILE_COOLDOWN):
                            region = motion_region(prev, gray)
                            if region:
                                st._tile_try = now
                                st._tile_busy = True
                                _pools()[1].submit(st._tile_job, region)
                        _gate_stats["total"] += 1
                        if gate_on and not misca and now - st._last_infer < MOTION_HEARTBEAT:
                            _gate_stats["gated"] += 1
                            continue
                        st._last_infer = now
                        lot.append((cid, img, st))
                    if lot:
                        import metrici
                        with metrici.cronometru("detect"), _lock:
                            # _get_model aici -> hot-reload dupa fine-tune
                            results = _get_model().predict(
                                [img for _, img, _ in lot], imgsz=640, conf=CONF,
                                classes=list(CLASSES), device=_device, verbose=False)
                        for (cid, img, st), res in zip(lot, results):
                            try:
                                st.process(img, res, now)
                            except Exception as e:
                                logare.info(f"[detect] cam{cid}: {str(e)[:100]}")
            except Exception as e:
                logare.info(f"[detect] bucla de lot: {str(e)[:100]}")
            time.sleep(max(0.2, INTERVAL - (time.time() - t0)))


def _ext_cam(cam_id):
    try:
        with open(EXT_FILE) as f:
            cams = json.load(f)
    except (FileNotFoundError, ValueError):
        return None
    return next((c for c in cams if c["id"] == cam_id), None)


detectors = {}


def load_cfg():
    """{cam_id_str: {"on": bool, "auto_siren": bool}}; migreaza formatul vechi (bool simplu)."""
    try:
        with open(CFG_FILE) as f:
            cfg = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    return {k: (v if isinstance(v, dict) else {"on": bool(v), "auto_siren": False})
            for k, v in cfg.items()}


def save_cfg(cfg):
    import store
    store.atomic_write_json(CFG_FILE, cfg, mode=0o600, indent=2)


def cam_cfg(cam_id):
    return load_cfg().get(str(cam_id), {})


def enabled():
    return {int(k) for k, v in load_cfg().items() if v.get("on")}


def auto_siren_on():
    return {int(k) for k, v in load_cfg().items() if v.get("auto_siren")}


def set_detect(cam_id, on=None, auto_siren=None):
    cfg = load_cfg()
    entry = cfg.get(str(cam_id), {"on": False, "auto_siren": False})
    if on is not None:
        entry["on"] = bool(on)
    if auto_siren is not None:
        entry["auto_siren"] = bool(auto_siren)
    cfg[str(cam_id)] = entry
    save_cfg(cfg)
    sync()


def set_zone(cam_id, points):
    """Zona de detectie: lista de puncte normalizate [[x,y],...] sau None (toata imaginea)."""
    cfg = load_cfg()
    entry = cfg.get(str(cam_id), {"on": False, "auto_siren": False})
    if points:
        entry["zone"] = [[round(float(x), 4), round(float(y), 4)] for x, y in points]
    else:
        entry.pop("zone", None)
    cfg[str(cam_id)] = entry
    save_cfg(cfg)


def _in_zone(poly, x, y):
    """Ray casting: e punctul normalizat (x,y) in poligon?"""
    inside = False
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


_loop = None

# Cine gazduieste bucla de detectie. False in nvr-web; nvr-ai o seteaza True la pornire
# (#9 pas 2). Acelasi modul ruleaza in ambele procese — doar gazda porneste bucla;
# in web sync() o tine oprita cat timp ai_service e activ (fallback daca e oprit).
IS_HOST = False


def sync():
    """Aliniaza starile per-camera la data/detect.json si tine bucla de lot vie.
    Cand ai_service e pornit, bucla traieste in nvr-ai — in web ramane oprita."""
    global _loop
    if not IS_HOST and settings.get("ai_service"):
        detectors.clear()
        if _loop is not None:
            _loop.stop_flag = True
            _loop = None
        return
    want = enabled()
    for cid in list(detectors):
        if cid not in want:
            detectors.pop(cid, None)
    for cid in want:
        if cid not in detectors:
            detectors[cid] = CamState(cid)
    if detectors and (_loop is None or not _loop.is_alive()):
        _loop = BatchLoop()
        _loop.start()


def gate_ratio():
    """Fractia de cadre sarite de motion-gate (0..1) — cat de statica e scena, global."""
    t = _gate_stats["total"]
    return round(_gate_stats["gated"] / t, 3) if t else 0.0


def snapshot():
    """{cam_id: {labels, recent, boxes?, boxes_age?}} pentru /api/events.
    Chenarele apar doar cat sunt proaspete; boxes_age creste cand motion-gate
    tine camera pe loc (scena statica) — overlay-ul le estompeaza."""
    now = time.time()
    out = {}
    for cid, d in detectors.items():
        e = {"labels": [l for l, ts in d.active.items() if now - ts < ACTIVE_TTL],
             "recent": list(d.recent)[:20]}
        if now - d.last_boxes["t"] < 12:
            e["boxes"] = d.last_boxes["boxes"]
            e["boxes_age"] = round(now - d.last_boxes["t"], 1)
        out[cid] = e
    return out
