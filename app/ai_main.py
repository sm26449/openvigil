#!/usr/bin/env python3
"""Serviciul de inferenta nvr-ai: modelele AI intr-un singur proces.

Frate cu nvr-record/nvr-go2rtc: proces separat, pornit de systemd, isi trage singur
cadrele. Detine modelele o singura data: YOLO partajat (tracking + chenare pe clip),
iar de la #9 pas 1 si Face ID (InsightFace + anti-spoof), LPR (fast-alpr) si
descrierea VLM (Qwen, lazy) — servite on-demand catre nvr-web (/face, /lpr,
/describe). NU detine motorul de reguli/MQTT: evenimentele pleaca la nvr-web
(/api/internal/emit), care le trece prin hooks.emit ca pe orice eveniment; in acest
proces hooks.emit e REDIRECTAT acolo (vezi _shim_output_plane). Un singur proprietar
al output-plane-ului. Contractul complet: docs/ai-contract.md.

Expune o stare locala (/state, /health, /capabilities) pe :8077 pe care nvr-web o
proxeaza (/api/track etc.) cand ai_service e pornit.
"""
import hashlib
import json
import os
import re
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests
from flask import Flask, jsonify, request

import ai_track
import settings
import track
import logare
import config as C
from i18n import t

CONTRACT = 1        # versiunea contractului web<->ai (docs/ai-contract.md)
START = time.time()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = f"http://127.0.0.1:{C.WEB_PORT}"
TOKEN_FILE = os.path.join(ROOT, "data", "ai_token")
AI_PORT = C.AI_PORT

TOKEN = ""


def _wait_token():
    """Asteapta ca nvr-web sa creeze jetonul partajat (data/ai_token)."""
    for _ in range(60):
        try:
            with open(TOKEN_FILE) as f:
                t = f.read().strip()
                if t:
                    return t
        except FileNotFoundError:
            pass
        time.sleep(2)
    return ""


SPOOL_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ai_spool.jsonl")
SPOOL_MAX = 20000
_spool_lock = threading.Lock()
_spool_thread = None


def _trimite(payload, timeout=5):
    """True daca web-ul a acceptat evenimentul (200); 4xx = respins definitiv (tot True,
    nu reincercam un eveniment pe care web-ul il refuza); False = web indisponibil."""
    try:
        r = requests.post(f"{WEB}/api/internal/emit", timeout=timeout, json=payload)
        if r.status_code != 200:
            logare.info(f"[nvr-ai] emit {payload.get('tip')} cam{payload.get('cam')}: HTTP {r.status_code}")
        return r.status_code < 500
    except Exception as e:
        logare.info(f"[nvr-ai] emit {payload.get('tip')} cam{payload.get('cam')}: {str(e)[:60]}")
        return False


def _spool_add(payload):
    """Web-ul e oprit (restart dupa setari, deploy): evenimentul asteapta pe disc si
    pleaca la reluare — altfel detectiile din acel minut dispareau fara urma."""
    global _spool_thread
    with _spool_lock:
        try:
            with open(SPOOL_FILE, "a") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError as e:
            logare.info(f"[nvr-ai] spool: {str(e)[:60]}")
            return
        if _spool_thread is None or not _spool_thread.is_alive():
            _spool_thread = threading.Thread(target=_spool_flush, daemon=True, name="ai-spool")
            _spool_thread.start()


def _spool_flush():
    """Reincearca la 15 s; cand web-ul revine, evenimentele pleaca in ordine, cu momentul
    lor original (`date.moment_spool`) ca jurnalul sa le dateze corect."""
    while True:
        time.sleep(15)
        with _spool_lock:
            try:
                with open(SPOOL_FILE) as f:
                    lines = f.readlines()
            except FileNotFoundError:
                return
            if not lines:
                try:
                    os.remove(SPOOL_FILE)
                except OSError:
                    pass
                return
        trimise = 0
        for ln in lines[:500]:
            try:
                payload = json.loads(ln)
            except ValueError:
                trimise += 1
                continue
            if not _trimite(payload, timeout=8):
                break
            trimise += 1
        with _spool_lock:
            rest = lines[trimise:][-SPOOL_MAX:]
            try:
                if rest:
                    with open(SPOOL_FILE, "w") as f:
                        f.writelines(rest)
                else:
                    os.remove(SPOOL_FILE)
            except OSError:
                pass
        if trimise:
            logare.info(f"[nvr-ai] spool: {trimise} evenimente livrate, {len(rest)} in asteptare")
        if not rest:
            return


def emit(tip, cam, date):
    """Trimite evenimentul la nvr-web (proprietarul reguli/MQTT/jurnal); daca web-ul nu
    raspunde, il pune in spool si il livreaza cand revine."""
    payload = {"token": TOKEN, "tip": tip, "cam": cam, "date": date}
    if not _trimite(payload):
        payload["date"] = {**(date or {}), "moment_spool": time.strftime("%Y-%m-%d %H:%M:%S")}
        _spool_add(payload)


def _shim_output_plane():
    """Modulele mutate aici (faces/plates/describe/detect) cheama hooks.emit si
    semantic.enqueue ca si cand ar rula in web. In procesul asta le redirectam:
    evenimentele merg la web (singurul proprietar al reguli/jurnal/MQTT/webhook),
    indexarea semantica la fel. Notificarile (notify.face/plate/detection) raman
    locale — dupa mutare, DOAR nvr-ai le mai emite, deci cooldown-urile au un
    singur stapan."""
    import hooks
    hooks.emit = lambda eveniment, cam_id=None, **date: emit(eveniment, cam_id, date)
    # AICI modelele sunt locale: dispatch-urile din detect (face/lpr/describe) nu
    # trebuie sa mai treaca prin HTTP catre... noi insine
    import ai_client
    ai_client.remote = lambda: False
    import detect
    detect.IS_HOST = True     # bucla de detectie traieste in procesul asta (#9 pas 2)
    import semantic
    semantic.IS_HOST = True   # indexul SigLIP traieste tot aici (#9 pas 3)


def _cam_entry(cam_id):
    """Camera standalone din data/cameras.json (FS comun) — pentru cadrul HD."""
    try:
        with open(os.path.join(ROOT, "data", "cameras.json")) as f:
            return next((c for c in json.load(f) if c["id"] == cam_id), None)
    except (OSError, ValueError):
        return None


def _body_img():
    """Corpul cererii (JPEG) -> imagine BGR, sau None daca lipseste/nu se decodeaza."""
    data = request.get_data()
    if not data:
        return None
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)


def _guard():
    """Localhost + jeton (query pentru corpuri binare, body pentru JSON).
    Intoarce raspunsul de refuz sau None daca cererea e in regula."""
    import hmac as _hmac
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "doar local"}), 403
    tok = request.args.get("token") or (request.get_json(silent=True) or {}).get("token", "")
    if not _hmac.compare_digest(str(tok or ""), TOKEN):
        return jsonify({"error": "jeton greșit"}), 401
    return None


def watch_settings():
    """Re-sincronizeaza la schimbari scrise de UI (web) pe FS-ul comun:
    settings.json (track_cams...), detect.json (detectia per camera) si fisierul
    de model (activate/revert dupa fine-tune -> hot-reload aici, unde ruleaza)."""
    import detect
    last = {}

    def _mtime(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return None

    while True:
        # sync-urile sunt idempotente si ieftine — le chemam la FIECARE iteratie,
        # nu doar la mtime: asa o bucla moarta (exceptie scapata) reinvie in <=3s
        # in loc sa stea moarta o zi cu /health verde (lectia din 2026-09-09)
        m = _mtime(settings.FILE)
        vie = ai_track._loop is not None and ai_track._loop.is_alive()
        if m != last.get("settings") or not vie:
            last["settings"] = m
            if not vie and ai_track._loop is not None:
                logare.warning("[nvr-ai] bucla de urmarire moarta — o repornesc")
            try:
                ai_track.sync(emit)
            except Exception as e:
                logare.info(f"[nvr-ai] sync: {str(e)[:80]}")
        m = _mtime(detect.CFG_FILE)
        vie = detect._loop is not None and detect._loop.is_alive()
        if m != last.get("detect") or not vie:
            last["detect"] = m
            if not vie and detect._loop is not None:
                logare.warning("[nvr-ai] bucla de detectie moarta — o repornesc")
            try:
                detect.sync()
            except Exception as e:
                logare.info(f"[nvr-ai] detect.sync: {str(e)[:80]}")
        m = _mtime(detect.MODEL_PATH)
        if last.get("model") is not None and m != last.get("model"):
            logare.info("[nvr-ai] model YOLO schimbat pe disc — reincarc")
            detect.reload_model()
            with ai_track._init_lock:      # si modelul partajat de tracking
                ai_track._model = None
        last["model"] = m
        # curba VRAM in jurnal, la ~30 min: cine creste, torch sau "altii" (onnx)?
        if time.time() - last.get("vram_t", 0) > 1800:
            last["vram_t"] = time.time()
            v = _vram()
            if v:
                logare.info(f"[nvr-ai] vram: liber {v['liber_mb']}MB, "
                      f"torch {v['torch_rezervat_mb']}MB (alocat {v['torch_alocat_mb']}), "
                      f"altii {v['folosit_de_altii_mb']}MB")
                # plasa de siguranta pe langa expandable_segments: blocurile din cache
                # fara proprietar (rezervat >> alocat, fragmentarea loturilor variabile)
                # se dau inapoi — DUPA log, ca esantionul sa arate acumularea ferestrei
                if v["torch_rezervat_mb"] - v["torch_alocat_mb"] > 512:
                    import torch
                    torch.cuda.empty_cache()
        time.sleep(3)


app = Flask(__name__)


@app.route("/health")
def health():
    import detect
    import reid
    loop = getattr(ai_track, "_loop", None)
    dloop = getattr(detect, "_loop", None)
    return jsonify({"ok": True, "cams": sorted(track.enabled_cams()),
                    "urmarite": len(loop.cams) if loop else 0,
                    "scene": sorted(loop.scenes) if loop else [],
                    # firele chiar TRAIESC? (/health verde cu bucla moarta = 2026-09-09)
                    "bucla_track": bool(loop and loop.is_alive()),
                    "bucla_detect": bool(dloop and dloop.is_alive()),
                    "reid": reid.snapshot()})


@app.route("/state")
def state():
    return jsonify(ai_track.snapshot())


@app.route("/detect_clip", methods=["POST"])
def detect_clip():
    """Chenare pe un clip (re-detectie pe modelul partajat GPU). Web trimite calea clipului
    (acelasi FS) + jeton; intoarce {fps, frames}. Localhost only."""
    import hmac as _hmac
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "doar local"}), 403
    b = request.get_json(silent=True) or {}
    if not _hmac.compare_digest(str(b.get("token", "")), TOKEN):
        return jsonify({"error": "jeton greșit"}), 401
    path = b.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"error": "clip inexistent"}), 404
    try:
        return jsonify(ai_track.clip_detect(path, int(b.get("fps", 4)),
                                            int(b.get("imgsz", 640))))
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


# ---- modele on-demand (#9 pas 1): Face ID, LPR, descriere VLM ----
@app.route("/face/process", methods=["POST"])
def face_process():
    """?cam=N, corp=JPEG (fallback; cadrul HD si-l trage singur de pe camera).
    Ruleaza Face ID complet (recunoastere + anti-spoof + crop-uri + notificari);
    evenimentele ajung la web prin shim. Intoarce {gasiti: [nume...]}."""
    err = _guard()
    if err:
        return err
    import faces
    cam_id = request.args.get("cam", type=int)
    try:
        return jsonify({"gasiti": faces.process(cam_id, _body_img(), _cam_entry(cam_id))})
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


@app.route("/face/known", methods=["POST"])
def face_known():
    """Corp=JPEG -> {nume} (sau null): gating-ul de intrus al camctl."""
    err = _guard()
    if err:
        return err
    import faces
    img = _body_img()
    try:
        return jsonify({"nume": faces.known_face(img) if img is not None else None})
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


@app.route("/face/enroll", methods=["POST"])
def face_enroll():
    """?name=, corp=JPEG -> inroleaza (modelul + biblioteca stau aici)."""
    err = _guard()
    if err:
        return err
    import faces
    try:
        return jsonify(faces.enroll(request.args.get("name") or "", request.get_data()))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/face/auto_enroll", methods=["POST"])
def face_auto_enroll():
    err = _guard()
    if err:
        return err
    import faces
    try:
        return jsonify({"rezidenti": faces.auto_enroll_residents()})
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


@app.route("/face/unknown_groups", methods=["POST"])
def face_unknown_groups():
    """Fetele necunoscute grupate pe persoana + sugestia din biblioteca (model + cache aici)."""
    err = _guard()
    if err:
        return err
    import faces
    try:
        return jsonify({"grupuri": faces.unknown_groups()})
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


@app.route("/face/regrupeaza", methods=["POST"])
def face_regrupeaza():
    """Regrupeaza fetele necunoscute acum (id-uri stabile) si intoarce grupuri.json."""
    err = _guard()
    if err:
        return err
    import faces
    try:
        return jsonify(faces.regrupeaza())
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


@app.route("/face/uneste", methods=["POST"])
def face_uneste():
    """Body: {token, a, b} — persoana b intra in a (poze mutate, embeddings refacute)."""
    err = _guard()
    if err:
        return err
    import faces
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, **faces.uneste(b.get("a") or "", b.get("b") or "")})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/face/rename", methods=["POST"])
def face_rename():
    """Body: {token, vechi, nou} — redenumeste o persoana din biblioteca."""
    err = _guard()
    if err:
        return err
    import faces
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True,
                        "nume": faces.rename_person(b.get("vechi") or "",
                                                    b.get("nou") or "")})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/face/delete", methods=["POST"])
def face_delete():
    """?name=&photo= — stergerea unei poze reconstruieste embedding-ul (model)."""
    err = _guard()
    if err:
        return err
    import faces
    try:
        faces.delete_person(request.args.get("name") or "", request.args.get("photo"))
        return jsonify({"ok": True})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@app.route("/lpr/process", methods=["POST"])
def lpr_process():
    """?cam=N, corp=JPEG (fallback) -> {numere: [...]} + toate efectele (crop, jurnal,
    notificare, eveniment prin web)."""
    err = _guard()
    if err:
        return err
    import plates
    cam_id = request.args.get("cam", type=int)
    try:
        return jsonify({"numere": plates.process(cam_id, _body_img(), _cam_entry(cam_id))})
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


@app.route("/describe/<kind>", methods=["POST"])
def describe_job(kind):
    """kind=person|vehicle; ?cam=&boxes=<json>&moment=, corp=JPEG. VLM-ul e lent
    (secunde-zeci de secunde) -> fir separat, raspundem imediat {ok}."""
    err = _guard()
    if err:
        return err
    import describe
    cam_id = request.args.get("cam", type=int)
    img = _body_img()
    if img is None:
        return jsonify({"error": "lipsește cadrul"}), 400
    try:
        boxes = json.loads(request.args.get("boxes") or "[]")
    except ValueError:
        return jsonify({"error": "boxes invalid"}), 400
    if kind == "person":
        threading.Thread(target=describe.process, args=(cam_id, img, boxes),
                         daemon=True).start()
    elif kind == "vehicle":
        threading.Thread(target=describe.process_vehicle,
                         args=(cam_id, img, boxes, request.args.get("moment")),
                         daemon=True).start()
    else:
        return jsonify({"error": "kind necunoscut"}), 404
    return jsonify({"ok": True})


# ---- hot-swap formal (faza 6): schimbarea modelului e o tranzactie verificata ----
@app.route("/model/verifica", methods=["POST"])
def model_verifica():
    """Pre-flight pe un CANDIDAT (body: {token, path}, FS comun), inainte de swap:
    se incarca? are clasele contractului (macar person + un vehicul)? infereaza?
    Ruleaza pe CPU — validarea nu are voie sa muste din VRAM-ul productiei."""
    err = _guard()
    if err:
        return err
    import detect
    b = request.get_json(silent=True) or {}
    path = b.get("path")
    if not path or not os.path.exists(path):
        return jsonify({"error": t("candidatul nu există")}), 404
    try:
        import numpy as np
        from ultralytics import YOLO
        m = YOLO(path)
        clase = set(m.names.values()) & detect.INTERESTING
        if "person" not in clase or not (clase & {"car", "truck", "bus"}):
            return jsonify({"error": t("modelul nu acopera clasele contractului (are doar: {clase})",
                                       clase=sorted(clase))}), 422
        m.to("cpu")
        m.predict(np.zeros((320, 320, 3), "uint8"), imgsz=320,
                  device="cpu", verbose=False)
        del m
        return jsonify({"ok": True, "clase": sorted(clase), "sha256": _sha(path)})
    except Exception as e:
        return jsonify({"error": t("candidatul nu trece verificarea: {e}", e=str(e)[:120])}), 422


@app.route("/model/reincarca", methods=["POST"])
def model_reincarca():
    """Dupa swap-ul fisierului: reincarca AICI (unde ruleaza detectia), FORTAT si
    sincron — intoarce amprenta si clasele modelului care chiar serveste acum.
    Fara asta, "activate" spunea ok iar incarcarea reala ramanea la voia primului
    cadru cu miscare."""
    err = _guard()
    if err:
        return err
    import numpy as np
    import detect
    try:
        detect.reload_model()
        with ai_track._init_lock:
            ai_track._model = None
        with detect._lock:
            detect._get_model().predict(np.zeros((320, 320, 3), "uint8"),
                                        imgsz=320, verbose=False)
        return jsonify({"ok": True, "amprenta": detect.amprenta(),
                        "clase": sorted(detect.CLASSES.values())})
    except Exception as e:
        return jsonify({"error": t("reincarcarea a eșuat: {e}", e=str(e)[:120])}), 500


@app.route("/adnotare/nesigure/start", methods=["POST"])
def nesigure_start():
    """Body: {token, n} -> scaneaza cele mai noi n cadre brute cu modelul activ (fir in fundal)."""
    err = _guard()
    if err:
        return err
    import nesigure
    b = request.get_json(silent=True) or {}
    n = max(10, min(20000, int(b.get("n") or 500)))
    return jsonify({"ok": nesigure.scaneaza(n), **nesigure.stare})


@app.route("/adnotare/nesigure/stare")
def nesigure_stare():
    err = _guard()
    if err:
        return err
    import nesigure
    return jsonify(nesigure.stare)


def _reid_query_emb(b):
    """Embedding-ul de interogare: din poza unui eveniment (data/snapshots/<zi>/<fn>, cel mai
    mare om din cadru, sau box-ul dat) sau dintr-un esantion al arhivei (zi + fn)."""
    import cv2
    import numpy as np
    import reid
    import reid_arhiva
    if b.get("crop_zi") and b.get("crop_fn"):
        e = reid_arhiva.embedding_din(b["crop_zi"], b["crop_fn"])
        if e is None:
            raise ValueError(t("esantionul nu mai e in arhivă"))
        return e, None
    poza = b.get("poza") or ""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})/(\d{6}_cam\d+_[A-Za-z0-9_.-]+\.jpg)$", poza)
    if not m:
        raise ValueError(t("poza invalidă"))
    cale = os.path.join(ROOT, "data", "snapshots", m[1], m[2])
    img = cv2.imread(cale)
    if img is None:
        raise ValueError(t("poza nu există"))
    h, w = img.shape[:2]
    box = b.get("box")
    try:   # box-ul vine de la client: 4 numere in [0..1], altfel il ignoram (sau 400 daca e gunoi)
        box = [float(v) for v in box] if box and len(box) == 4 else None
        if box and not all(0.0 <= v <= 1.0 for v in box):
            box = None
    except (TypeError, ValueError):
        raise ValueError(t("box invalid"))
    if not box:
        # chenarele de la momentul detectiei (sidecar-ul pozei): cel mai mare om, fara re-detectie
        try:
            import json as _json
            with open(cale + ".json", encoding="utf-8") as f:
                sc = _json.load(f)
            oameni = [bb for bb in sc.get("boxes", []) if bb.get("cls") == "person"]
            if oameni:
                bb = max(oameni, key=lambda q: q["w"] * q["h"])
                box = [bb["x"], bb["y"], bb["w"], bb["h"]]
        except (OSError, ValueError):
            pass
    if box:
        x1, y1, x2, y2 = (int(box[0] * w), int(box[1] * h), int((box[0] + box[2]) * w), int((box[1] + box[3]) * h))
    else:
        import detect
        # poze vechi, fara sidecar: re-detectie la rezolutia nativa (omul poate fi mic pe panorama)
        sz = min(2560, (max(h, w) + 31) // 32 * 32)
        boxes = detect.persoane(img, conf=0.3, imgsz=sz)
        if not boxes:
            raise ValueError(t("niciun om găsit în poză (poză veche, fără chenarele salvate)"))
        x1, y1, x2, y2 = max(boxes, key=lambda bb: (bb[2] - bb[0]) * (bb[3] - bb[1]))
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
    crop = img[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    if crop.size == 0 or crop.shape[0] < 48:
        raise ValueError(t("omul e prea mic în poză"))
    e = reid.embed([crop])[0]
    ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return e, (buf.tobytes() if ok else None)


@app.route("/reid/cauta", methods=["POST"])
def reid_cauta():
    """Body: {token, poza: "<zi>/<fn>", box?: [x,y,w,h] normalizat} sau {crop_zi, crop_fn};
    zile?, limit?, vizibile? -> aparitiile corpului pe toate camerele."""
    err = _guard()
    if err:
        return err
    import base64
    import reid_arhiva
    b = request.get_json(silent=True) or {}
    try:
        emb, crop_jpg = _reid_query_emb(b)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500
    vis = b.get("vizibile")
    hits = reid_arhiva.cauta(emb, zile_max=int(b.get("zile") or 7), limit=int(b.get("limit") or 60),
                             vizibile=None if vis is None else {int(c) for c in vis})
    return jsonify({"aparitii": hits, "interogare": (base64.b64encode(crop_jpg).decode() if crop_jpg else None),
                    "arhiva": reid_arhiva.stare()})


@app.route("/reid/stare")
def reid_stare():
    err = _guard()
    if err:
        return err
    import reid_arhiva
    return jsonify(reid_arhiva.stare())


@app.route("/train/stare")
def train_stare():
    """Jobul de antrenament curent / ultimul (dataset sau antrenare), cu progres si log."""
    err = _guard()
    if err:
        return err
    import antrenare
    return jsonify(antrenare.curent())


@app.route("/train/start", methods=["POST"])
def train_start():
    """Body: {token, tip: dataset|antrenare, epoci?, max?} -> porneste subprocesul (unul odata)."""
    err = _guard()
    if err:
        return err
    import antrenare
    b = request.get_json(silent=True) or {}
    try:
        return jsonify({"ok": True, **antrenare.porneste(b.get("tip") or "", b.get("epoci"),
                                                          b.get("max"))})
    except (RuntimeError, ValueError) as e:
        return jsonify({"error": str(e)}), 409


@app.route("/train/stop", methods=["POST"])
def train_stop():
    err = _guard()
    if err:
        return err
    import antrenare
    return jsonify({"ok": antrenare.opreste()})


@app.route("/metrici")
def metrici_snapshot():
    """Metrici per model (faza 6): apeluri, rata pe minut, latenta medie/ultima —
    "GPU la 73%" nu spune CINE munceste; asta spune. + coada indexului semantic."""
    import metrici
    import semantic
    out = metrici.snapshot()
    out["semantic_coada"] = semantic._queue.qsize()
    return jsonify(out)


@app.route("/detect/state")
def detect_state():
    """Starea detectiei (badge-uri active + istoricul recent) + rata motion-gate +
    obiectele stationare — web-ul o proxeaza in /api/events, /api/pulse, /api/system."""
    import detect
    import stationar
    return jsonify({"detect": detect.snapshot(), "gate_ratio": detect.gate_ratio(),
                    "stationare": stationar.snapshot()})


@app.route("/detect/confirm", methods=["POST"])
def detect_confirm():
    """?cam=N — confirmarea de intrus la perimetru: YOLO pe cadrul cald + HD, plus
    gating-ul Face ID (persoana cunoscuta nu e intrus) — ambele modele sunt aici.
    Intoarce {gasit, nume, adnotat(b64)}; decizia si emiterea raman in web (camctl)."""
    err = _guard()
    if err:
        return err
    import base64
    import detect
    cam_id = request.args.get("cam", type=int)
    try:
        gasit, adnotat, img = detect.confirm_alarm(cam_id)
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500
    nume = None
    if "person" in gasit and img is not None:
        try:
            import faces
            nume = faces.known_face(img)
        except Exception as e:
            logare.info(f"[nvr-ai] known_face cam{cam_id}: {str(e)[:60]}")
    out = {"gasit": gasit, "nume": nume}
    if adnotat is not None:
        import cv2
        ok, buf = cv2.imencode(".jpg", adnotat, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if ok:
            out["adnotat"] = base64.b64encode(buf.tobytes()).decode()
    return jsonify(out)


# ---- indexul semantic + intrebari (#9 pas 3): SigLIP traieste aici ----
@app.route("/semantic/index", methods=["POST"])
def semantic_index():
    """Body: {token, day, fn} — un snapshot nou (scris de web pe FS-ul comun)
    intra in index. Oglinda lui /api/internal/index de dinainte de pas 3."""
    err = _guard()
    if err:
        return err
    import re
    import semantic
    b = request.get_json(silent=True) or {}
    day, fn = str(b.get("day") or ""), str(b.get("fn") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day) or not re.match(r"^[\w.-]+\.jpg$", fn):
        return jsonify({"error": "cerere invalida"}), 400
    try:
        with open(os.path.join(ROOT, "data", "snapshots", day, fn), "rb") as f:
            data = f.read()
    except OSError:
        return jsonify({"error": "fișier inexistent"}), 404
    semantic.enqueue(day, fn, data)
    return jsonify({"ok": True})


@app.route("/semantic/prune", methods=["POST"])
def semantic_prune():
    err = _guard()
    if err:
        return err
    import semantic
    semantic.prune()
    return jsonify({"ok": True})


@app.route("/semantic/search", methods=["POST"])
def semantic_search():
    """Body: {token, q, days, limit, visible} — cautare in index; visible = lista de
    camere permise sesiunii (None = toate), calculata de web (el stie utilizatorul)."""
    err = _guard()
    if err:
        return err
    import semantic
    b = request.get_json(silent=True) or {}
    vis = b.get("visible")
    try:
        rez = semantic.search((b.get("q") or "").strip(),
                              days=int(b.get("days") or 14),
                              limit=int(b.get("limit") or 40),
                              visible=None if vis is None else {int(c) for c in vis})
        return jsonify({"results": rez})
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


@app.route("/ask", methods=["POST"])
def ask_question():
    """Body: {token, q, visible, cam_names, cam} — intrebare libera peste index +
    jurnalul de evenimente (FS comun, doar citire aici)."""
    err = _guard()
    if err:
        return err
    import ask
    b = request.get_json(silent=True) or {}
    vis = b.get("visible")
    nume = {int(k): v for k, v in (b.get("cam_names") or {}).items()}
    try:
        return jsonify(ask.answer((b.get("q") or "").strip(),
                                  visible=None if vis is None else {int(c) for c in vis},
                                  cam_names=nume, cam=b.get("cam")))
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 500


@app.route("/semantic/status")
def semantic_status():
    import semantic
    return jsonify(semantic.status())


@app.route("/semantic/backfill", methods=["POST"])
def semantic_backfill():
    err = _guard()
    if err:
        return err
    import semantic
    started = semantic.start_backfill()
    return jsonify({"ok": True, "started": started, **semantic.backfill_state})


@app.route("/warmup", methods=["POST"])
def warmup():
    """Body: {face?, lpr?, describe?, semantic?} — preincarca modelele cerute (async)."""
    err = _guard()
    if err:
        return err
    b = request.get_json(silent=True) or {}
    if b.get("face"):
        import faces
        faces.warmup_async()
    if b.get("lpr"):
        import plates
        plates.warmup_async()
    if b.get("describe"):
        import describe
        describe.warmup_async()
    if b.get("semantic"):
        import semantic
        semantic.warmup_async()
    return jsonify({"ok": True})


def _vram():
    """Defalcarea VRAM din perspectiva procesului: ce tine torch (modelele PyTorch:
    YOLO/SigLIP/Qwen) vs restul (arenele onnxruntime: InsightFace/spoof/ALPR +
    contextele CUDA). Diferenta 'folosit_de_altii' care CRESTE in timp = arenele
    onnx care nu dau memoria inapoi — exact clasa de scurgere care a lasat Qwen
    fara loc la reincarcare (2026-09-02, 21:01)."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        aloc = torch.cuda.memory_allocated()
        rez = torch.cuda.memory_reserved()
        return {"liber_mb": free // 2**20, "total_mb": total // 2**20,
                "torch_alocat_mb": aloc // 2**20, "torch_rezervat_mb": rez // 2**20,
                "folosit_de_altii_mb": (total - free - rez) // 2**20}
    except Exception:
        return None


_sha_cache = {}


def _sha(path):
    """SHA-256 al unui fisier de model, cache-uit pe (mtime, marime) — fingerprinting:
    ce versiune de model a produs o detectie (probe mai solide, hot-swap verificabil)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    key = (st.st_mtime, st.st_size)
    if _sha_cache.get(path, (None,))[0] != key:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _sha_cache[path] = (key, h.hexdigest())
    return _sha_cache[path][1]


@app.route("/capabilities")
def capabilities():
    """Contractul viu: ce stie serviciul, ce modele are (incarcate sau lazy) si
    amprenta lor. docs/ai-contract.md descrie schema; CONTRACT creste la schimbari
    incompatibile."""
    import describe
    import detect
    import faces
    import plates
    import semantic
    import verify
    s = settings.load()
    return jsonify({
        "serviciu": "nvr-ai", "contract": CONTRACT,
        "pornit_de_s": int(time.time() - START),
        "vram": _vram(),
        "permisiuni": ["cadre:go2rtc+isapi", "evenimente:web:/api/internal/emit",
                       "index:web:/api/internal/index",
                       "fs:data/snapshots,data/faces,data/plates,data/tmp"],
        "modele": {
            "yolo": {"activ": True, "incarcat": ai_track._model is not None,
                     "cale": detect.MODEL_PATH, "sha256": _sha(detect.MODEL_PATH)},
            "verify": {"activ": True, "incarcat": verify._model is not None,
                       "cale": verify.STOCK_PATH, "sha256": _sha(verify.STOCK_PATH)},
            "face": {"activ": bool(s["face_enabled"]), "incarcat": faces._app is not None,
                     "antispoof": bool(s["face_antispoof"]),
                     "spoof_incarcat": faces._spoof_models is not None},
            "lpr": {"activ": bool(s["lpr_enabled"]), "incarcat": plates._alpr is not None},
            "describe": {"activ": bool(s["person_describe"]),
                         "incarcat": describe._model is not None,
                         "nota": t("lazy; se descarca dupa inactivitate")},
            "semantic": {"activ": bool(s["semantic_enabled"]),
                         "incarcat": semantic._model is not None,
                         "backfill": dict(semantic.backfill_state)},
        },
    })


def main():
    global TOKEN
    TOKEN = _wait_token()
    logare.info(f"[nvr-ai] pornit pe :{AI_PORT}, jeton={'OK' if TOKEN else 'LIPSA'}")
    _shim_output_plane()                      # evenimentele/indexarea -> web, nu local
    # preincarcari fara capcane de ordine (onnxruntime, lock propriu)
    if settings.get("face_enabled"):
        import faces
        faces.warmup_async()
    if settings.get("lpr_enabled"):
        import plates
        plates.warmup_async()

    def _modelele_apoi_buclele():
        # Ordinea stricta invatata in web: from_pretrained(bf16) tine dtype-ul global
        # pe bfloat16 cat incarca Qwen; un SigLIP incarcat in fereastra iese cu greutati
        # bf16 (cautarile pica), iar un predict YOLO prins in ea da "Got unsupported
        # ScalarType BFloat16". Deci: SigLIP -> Qwen -> buclele. Costul e ~un minut de
        # detectie amanata la restartul (rar al) lui nvr-ai.
        import semantic
        if semantic.enabled() or settings.get("person_describe"):
            import transformers  # noqa: F401  (import unic, inaintea firelor care il vor)
        if semantic.enabled():
            try:
                semantic._get_model()
            except Exception as e:
                logare.info(f"[nvr-ai] warmup semantic: {str(e)[:80]}")
        if settings.get("person_describe"):
            import describe
            try:
                describe._get_model()
            except Exception as e:
                logare.info(f"[nvr-ai] warmup describe: {str(e)[:80]}")
        ai_track.sync(emit)                   # urmaritorii dupa track_cams
        import detect
        detect.sync()                         # bucla de detectie (#9 pas 2) — AICI
        # watch-ul porneste ABIA acum: prima lui iteratie ar chema sync inainte de vreme
        threading.Thread(target=watch_settings, daemon=True).start()

    threading.Thread(target=_modelele_apoi_buclele, daemon=True).start()
    from waitress import serve
    serve(app, host="127.0.0.1", port=AI_PORT, threads=8)


if __name__ == "__main__":
    main()
