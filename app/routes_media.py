#!/usr/bin/env python3
"""Blueprint: media — clip/boxes, thumb, snapshots, recordings, play, export.

Al cincilea grup extras din server.py (audit #8). Helper-urile de randare/streaming
(_build_clip, render_clip, hik_rtsp, go2rtc_src, register_stream, _ts) raman in
`server` — sunt folosite si de alte module (routes_incidents importa render_clip).
Guard-ul de autentificare e `@app.before_request` global, deci se aplica si aici.
"""
import json
import os
import re
import time
import xml.etree.ElementTree as ET

from flask import (Blueprint, Response, jsonify, request, send_file,
                   send_from_directory)
import requests

import audit
import config as C
import jobs
import logare
import masca
import record
from i18n import t
from server import (HW, ROOT, _build_clip, _cam_entry, _ts, _visible_ids,
                    ai_token, current_user, get_cameras, go2rtc_src, hik_rtsp,
                    isapi, nvr_history_cam, register_stream, render_clip,
                    resolve_camera, strip_ns)

bp = Blueprint("media", __name__)


def _who():
    return (current_user() or {}).get("user")


def _clip_path(ch, t):
    return os.path.join(ROOT, "data", "tmp", f"clip_{ch}_{int(t)}.mp4")


@bp.route("/api/clip")
def api_clip():
    """?channel=&time=20260803T101500Z -> clip MP4 in jurul evenimentului (-5s..+15s),
    redabil in browser (H.264; HEVC se transcodeaza). Cache in data/tmp/.
    Daca nu e inca generat: 202 + jobul care il face (cu &job=1, un clip gata = {gata}).
    Interfata asteapta jobul si abia apoi pune URL-ul in <video>."""
    ch, t = int(request.args["channel"]), _ts(request.args["time"])
    path = _clip_path(ch, t)
    if os.path.exists(path):
        if request.args.get("job"):
            return jsonify({"gata": True})
        return send_file(path, mimetype="video/mp4")
    return jsonify(jobs.submit("clip", {"channel": ch, "time": int(t)}, who=_who(),
                               cam=ch)), 202


def _job_clip(job):
    path, err = _build_clip(job.parametri["channel"], job.parametri["time"])
    if err:
        raise jobs.JobError(err[0])
    return {"octeti": os.path.getsize(path)}


@bp.route("/api/clip/boxes")
def api_clip_boxes():
    """Re-detectie YOLO pe EXACT clipul redat -> chenare (normalizate 0..1) per moment,
    pentru overlay pe player. Rezultatul e cache-uit langa clip (.boxes.json); pana e
    gata, 202 + job (interfata re-cere ruta dupa ce jobul se termina)."""
    ch, t = int(request.args["channel"]), _ts(request.args["time"])
    cache = _clip_path(ch, t)[:-4] + ".boxes.json"
    if os.path.exists(cache):
        if request.args.get("job"):
            return jsonify({"gata": True})
        return send_file(cache, mimetype="application/json")
    return jsonify(jobs.submit("chenare", {"channel": ch, "time": int(t)}, who=_who(),
                               cam=ch)), 202


def _job_chenare(job):
    job.progres(5, t("pregatesc clipul"))
    path, err = _build_clip(job.parametri["channel"], job.parametri["time"])
    if err:
        raise jobs.JobError(err[0])
    job.progres(40, t("detectez"))
    data = _chenare(path)
    try:
        with open(path[:-4] + ".boxes.json", "w") as f:
            json.dump(data, f)
    except OSError:
        pass
    return {"cadre": len(data.get("frames", []))}


def _chenare(path):
    """Chenarele YOLO pe clipul `path` (sau JobError daca niciun model nu raspunde)."""
    import detect

    # Intai nvr-ai (model YOLO cald pe GPU): ~2-3s, fara VRAM in plus. Acelasi FS, deci
    # trimitem doar calea clipului. Daca nvr-ai nu raspunde -> fallback pe CPU aici (redus).
    data = None
    try:
        r = requests.post(f"http://127.0.0.1:{C.AI_PORT}/detect_clip", timeout=45,
                          json={"token": ai_token(), "path": path, "fps": 4, "imgsz": 640})
        if r.ok and "frames" in r.json():
            data = r.json()
    except requests.RequestException:
        pass

    if data is None:                                  # fallback local (CPU): mai putine cadre
        try:
            import cv2
            model = detect._get_model()
        except Exception as e:
            raise jobs.JobError(t("model indisponibil: {e}", e=str(e)[:80]))
        sample_fps = 2
        cap = cv2.VideoCapture(path)
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 15
        step = max(1, int(round(src_fps / sample_fps)))
        samp, times, idx = [], [], 0
        while True:
            ok, fr = cap.read()
            if not ok:
                break
            if idx % step == 0:
                samp.append(fr)
                times.append(round(idx / src_fps, 2))
            idx += 1
        cap.release()
        frames = []
        classes = list(detect.CLASSES) or None
        for i in range(0, len(samp), 16):
            for rt, res in zip(times[i:i + 16],
                               model.predict(samp[i:i + 16], imgsz=512, conf=detect.CONF,
                                             classes=classes, verbose=False)):
                h, w = res.orig_shape
                boxes = []
                for b in res.boxes:
                    x1, y1, x2, y2 = b.xyxy[0].tolist()
                    boxes.append({"x": round(x1 / w, 4), "y": round(y1 / h, 4),
                                  "w": round((x2 - x1) / w, 4), "h": round((y2 - y1) / h, 4),
                                  "lbl": model.names.get(int(b.cls[0]), "?"),
                                  "conf": round(float(b.conf[0]), 2)})
                frames.append({"t": rt, "boxes": boxes})
        data = {"fps": sample_fps, "frames": frames}
    return data


@bp.route("/api/thumb/<int:cam_id>")
def api_thumb(cam_id):
    """Imagine recenta a camerei — pentru pagina de administrare si liste, unde 18 (sau 100)
    de fluxuri video ar fi risipa. Vine din go2rtc, cache scurt in memoria browserului."""
    try:
        r = requests.get(f"{C.GO2RTC_API}/api/frame.jpeg",
                         params={"src": f"cam{cam_id}"}, timeout=12)
        if r.ok and r.content:
            resp = Response(r.content, mimetype="image/jpeg")
            resp.headers["Cache-Control"] = "public, max-age=20"
            return resp
    except requests.RequestException:
        pass
    return jsonify({"error": t("indisponibil")}), 502


DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@bp.route("/snapshots/<day>/<fn>")
def snapshot_files(day, fn):
    # <day> intra direct in calea de pe disc: fara validare, "../" urca din data/snapshots
    # si serveste orice fisier al aplicatiei (secret_key, users.json, cameras.json).
    if not DAY_RE.match(day):
        return jsonify({"error": t("zi invalida")}), 404
    vis = _visible_ids()
    if vis is not None:
        m = re.match(r"^\d{6}_cam(\d+)_", fn)
        if not m or int(m[1]) not in vis:
            return jsonify({"error": t("nu ai acces la această camera")}), 403
    return send_from_directory(os.path.join(ROOT, "data", "snapshots", day), fn)


_THUMB_CACHE = {}            # (day, fn) -> jpeg mic; cel mult _THUMB_MAX intrari
_THUMB_MAX = 600


@bp.route("/api/flux/thumb/<day>/<fn>")
def flux_thumb(day, fn):
    """Miniatura (latime 200 px) a unui snapshot, pentru fluxul de langa Live: 40 de poze
    intregi ar fi ~4 MB la fiecare incarcare. Aceleasi reguli de acces ca /snapshots."""
    if not DAY_RE.match(day) or not re.match(r"^\d{6}_cam(\d+)_[A-Za-z0-9_.-]+\.jpg$", fn):
        return jsonify({"error": t("cadru invalid")}), 404
    vis = _visible_ids()
    if vis is not None and int(re.match(r"^\d{6}_cam(\d+)_", fn)[1]) not in vis:
        return jsonify({"error": t("nu ai acces la această camera")}), 403
    key = (day, fn)
    data = _THUMB_CACHE.get(key)
    if data is None:
        import cv2
        img = cv2.imread(os.path.join(ROOT, "data", "snapshots", day, fn))
        if img is None:
            return jsonify({"error": t("nu există")}), 404
        h, w = img.shape[:2]
        k = 200 / max(1, w)
        img = cv2.resize(img, (200, max(1, int(h * k))), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            return jsonify({"error": t("nu pot genera miniatura")}), 500
        data = buf.tobytes()
        if len(_THUMB_CACHE) >= _THUMB_MAX:
            _THUMB_CACHE.pop(next(iter(_THUMB_CACHE)))
        _THUMB_CACHE[key] = data
    return Response(data, mimetype="image/jpeg", headers={"Cache-Control": "max-age=86400"})


# ---- cautare inregistrari ----
def _interval_valid(*vals):
    """True daca toate marcajele compacte (20260803T102030Z) date se parseaza. Marcajele
    ajung in URL-ul RTSP si in argumentele ffmpeg: le validam, nu le trecem mai departe."""
    try:
        for v in vals:
            if v:
                _ts(v)
        return True
    except ValueError:
        return False


ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

SEARCH_TMPL = """<?xml version="1.0" encoding="utf-8"?>
<CMSearchDescription>
<searchID>{sid}</searchID>
<trackList><trackID>{track}</trackID></trackList>
<timeSpanList><timeSpan><startTime>{start}</startTime><endTime>{end}</endTime></timeSpan></timeSpanList>
<maxResults>100</maxResults>
<searchResultPostion>{pos}</searchResultPostion>
</CMSearchDescription>"""


@bp.route("/api/recordings")
def api_recordings():
    """?channel=1&start=2026-06-01T00:00:00Z&end=2026-06-02T00:00:00Z"""
    ch = int(request.args["channel"])
    start = request.args["start"]
    end = request.args["end"]
    if not (ISO_RE.match(start) and ISO_RE.match(end)):   # intra in XML-ul trimis camerei
        return jsonify({"error": t("interval invalid")}), 400
    # intai inregistrarile locale de pe server
    segments = record.list_segments(ch, start[:10]) if record.has_recordings(ch) else []
    if segments:
        return jsonify({"channel": ch, "count": len(segments), "segments": segments})
    # ziua nu exista local -> istoricul de dinainte de migrare (NVR) sau SD-ul camerei
    entry = _cam_entry(ch) or {}
    cam = nvr_history_cam(ch) or (resolve_camera(ch) if entry.get("playback") else None)
    if not cam:
        return jsonify({"channel": ch, "count": 0, "segments": []})
    track = cam["main"]  # main stream (NVR: ch*100+1, standalone: 101)
    sid = f"{{C0FFEE00-0000-0000-0000-{ch:012d}}}"
    segments = []
    pos = 0
    for _ in range(50):  # max 5000 segmente
        body = SEARCH_TMPL.format(sid=sid, track=track, start=start, end=end, pos=pos)
        root = ET.fromstring(isapi("/ISAPI/ContentMgmt/search", "POST", body, cam=cam))
        status = more = None
        items = 0
        for el in root.iter():
            tag = strip_ns(el.tag)
            if tag == "responseStatusStrg":
                more = el.text
            if tag == "searchMatchItem":
                items += 1
                seg = {}
                for c in el.iter():
                    ct = strip_ns(c.tag)
                    if ct == "startTime":
                        seg["start"] = c.text
                    elif ct == "endTime":
                        seg["end"] = c.text
                    elif ct == "metadataDescriptor" and c.text:
                        seg["type"] = c.text.rsplit("/", 1)[-1]
                if "start" in seg:
                    segments.append(seg)
        pos += items
        if more != "MORE" or items == 0:
            break
    return jsonify({"channel": ch, "count": len(segments), "segments": segments})


@bp.route("/api/play")
def api_play():
    """?channel=1&start=20260601T100000Z&end=...  (playback)  sau  ?channel=1&hd=1 (live HD)
    Inregistratile sunt HEVC -> transcodam la H.264 pt. browser.
    &height=720/1080/0 alege treapta de rezolutie PER-CERERE (0 = nativ); lipsa =
    globalul PLAYBACK_HEIGHT. Doar trepte fixe: numele stream-ului contine treapta,
    deci clientii cu aceeasi treapta REFOLOSESC acelasi ffmpeg pe GPU — un height
    liber ar porni cate o transcodare per client."""
    ch = int(request.args["channel"])
    start = request.args.get("start")
    end = request.args.get("end")
    if not _interval_valid(start, end):
        return jsonify({"error": t("interval invalid")}), 400
    height = request.args.get("height", type=int)
    if height not in (0, 720, 1080):
        height = None                       # orice altceva -> comportamentul global
    hsuf = "" if height is None else f"_h{height}"
    cam = resolve_camera(ch)
    ext = _cam_entry(ch)
    # Audio pe fluxul HD DOAR cand camera chiar are o pista audio utila (mic + audio_ok),
    # ca in grila. Camerele cu mic=true dar audio_ok=false (intrare de linie fara microfon)
    # n-au pista audio: cerand 'audio=aac' pe ea, ffmpeg pierde ~1.8s cautand-o inainte de
    # primul cadru video (masurat cam7: 4.2s -> 2.3s fara audio).
    feat = (ext or {}).get("features", {})
    has_mic = bool(feat.get("mic") and feat.get("audio_ok"))
    hist = nvr_history_cam(ch)
    # Viteza de redare: 1x normal; 0.5x/2x/4x/8x prin template-uri dedicate (citire
    # accelerata/incetinita + comprimarea marcajelor de timp). Pe CPU lantul
    # decodare+reincodare merge la ~2.5x fata de timpul real, deci fara GPU limitam la 2x;
    # cu NVDEC merg fluid si 4x si 8x (vezi gen_go2rtc.py). `speed` = sufixul template-ului.
    speed = {"0.5": "05", "2": "2", "4": "4", "8": "8"}.get(request.args.get("speed", "1"), "")
    if speed in ("4", "8") and not HW:
        speed = "2"
    # Nu putem fi inregistrat viitorul. Fara verificarea asta, un clic pe banda dupa ora
    # curenta cadea pe ramura NVR si intorcea un flux gol, adica un player care se invarte
    # la nesfarsit fara sa spuna nimic.
    if start and _ts(start) > time.time() + 5:
        return jsonify({"error": t("momentul ales nu a venit încă")}), 404
    if start and record.has_recordings(ch):
        # playback din inregistrarile locale: concat + inpoint, prin template-ul vodcat
        lst = record.build_playlist(ch, _ts(start), _ts(end) if end else None)
        if lst:
            name = f"pb{ch}_{start}_{speed or 1}{hsuf}"
            # masca de confidentialitate: template-ul propriu al camerei (m<id>[s<viteza>]),
            # la rezolutie nativa (vezi masca.py)
            mascata = HW and masca.rects(ext)
            parts = [f"ffmpeg:{lst}",
                     f"input=vodcat{'n' if HW else ''}{speed}",
                     (f"video=m{ch}{'s' + speed if speed else ''}" if mascata
                      else f"video={'h264n' if HW else 'h264'}{speed}")]
            if has_mic and not speed:       # la alta viteza sunetul nu ajuta cu nimic
                parts.append("audio=aac")
            h = C.PLAYBACK_HEIGHT if height is None else height
            if h and not mascata:
                parts.append(f"height={h}")
            register_stream(name, "#".join(parts))
            return jsonify({"stream": name})
        if not hist:
            return jsonify({"error": t("nu există înregistrări la acel moment")}), 404
        cam = hist  # istoricul dinainte de migrare: il luam de pe NVR
    if start:
        name = f"pb{ch}_{start}{hsuf}"
        rtsp = hik_rtsp(cam, start, end)
    else:
        name = f"hd{ch}{hsuf}"
        rtsp = hik_rtsp(cam)  # live HD mereu direct de pe camera
    register_stream(name, go2rtc_src(rtsp, transcode=True, audio=has_mic, height=height,
                                     cam_id=ch))
    return jsonify({"stream": name})


@bp.route("/api/export/report")
def api_export_report():
    """Fisa probatorie a unui interval exportat: reface exact acelasi MP4, ii
    calculeaza SHA-256 si intoarce metadatele — ce trebuie alaturi de un fisier
    predat politiei ca sa i se poata dovedi integritatea. 202 + job."""
    ch = int(request.args["channel"])
    start, end = request.args["start"], request.args["end"]
    t0, t1 = _ts(start), _ts(end)
    if not 0 < t1 - t0 <= 1800:
        return jsonify({"error": t("interval invalid (între 1 sec și 30 min)")}), 400
    if not (record.has_recordings(ch) and record.build_playlist(ch, t0, t1)):
        return jsonify({"error": t("raportul se emite doar pentru înregistrări locale")}), 404
    return jsonify(jobs.submit("raport", {"channel": ch, "start": start, "end": end},
                               who=_who(), cam=ch)), 202


def _job_raport(job):
    import hashlib
    ch, start, end = (job.parametri[k] for k in ("channel", "start", "end"))
    t0, t1 = _ts(start), _ts(end)
    exp_job, livrat = _export_livrat(ch, start, end)
    if livrat:
        job.progres(50, t("hash pe fișierul exportat"))
        sha, octeti, sursa = _sha256(livrat), os.path.getsize(livrat), t("exportul {id} (fisierul livrat)", id=exp_job["id"])
    else:
        tmp = job.cale("mp4")
        ok, err = render_clip(ch, start, end, tmp, wm=False, job=job)
        if not ok:
            raise jobs.JobError(err)
        job.progres(95, t("calculez SHA-256"))
        sha, octeti, sursa = _sha256(tmp), os.path.getsize(tmp), t("regenerare identica (exportul nu mai era pe disc)")
        os.remove(tmp)                  # fisa e rezultatul; MP4-ul se descarca separat
    h = type("H", (), {"hexdigest": staticmethod(lambda: sha)})()
    lt = lambda x: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(x))
    goluri = [{"de_la": lt(a), "pana_la": lt(b), "secunde": int(b - a)} for a, b in record.goluri(ch, t0, t1)]
    cam = next((c for c in get_cameras() if c["id"] == ch), {})
    # lantul de custodie: segmentele-sursa cu amprentele lor de la INCHIDERE (nvr-record,
    # data/segmente.db) si verificarea lor acum — dovedeste ca materialul brut e neatins
    import lant
    surse = []
    for sg in lant.lista(ch, since=t0, until=t1):
        v = lant.verifica(ch, sg["nume"])
        surse.append({"segment": sg["nume"], "octeti": sg["octeti"], "sha256_la_inchidere": sg["sha256"],
                      "calculat_la": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(sg["calculat"])),
                      "neatins_acum": v.get("ok")})
    raport = {
        "sistem": C.NUME,
        "camera": {"id": ch, "nume": cam.get("name"), "model": cam.get("model")},
        "interval": {"de_la": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t0)),
                     "pana_la": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t1)),
                     "fus_orar": time.strftime("%Z")},
        "fisier": {"nume": f"cam{ch}_{start}.mp4", "octeti": octeti,
                   "sha256": h.hexdigest(), "sursa_hash": sursa},
        "goluri_inregistrare": goluri,
        "segmente_sursa": surse,
        "generat": time.strftime("%Y-%m-%d %H:%M:%S"),
        "generat_de": job.creat_de,
        "nota": t("Hash-ul SHA-256 dovedeste ca fisierul MP4 nu a fost modificat dupa export. Verificare: sha256sum <fisier>. `segmente_sursa` sunt fisierele brute din care s-a taiat clipul, cu amprenta calculata de serviciul de inregistrare la inchiderea fiecaruia si re-verificata la generarea fisei. `goluri_inregistrare` sunt intervalele fara material: clipul le sare (timpul din clip NU e continuu acolo)."),
    }
    audit.log("raport probatoriu", f"cam{ch} {start}-{end}",
              {"sha256": h.hexdigest()[:16]}, job.creat_de)
    out = job.cale("json")
    with open(out, "w") as f:
        json.dump(raport, f, indent=2, ensure_ascii=False)
    job.fisier(out, f"raport_cam{ch}_{start}.json", "application/json")
    return {"sha256": h.hexdigest(), "octeti": octeti}


@bp.route("/api/export")
def api_export():
    """?channel=&start=20260803T101500Z&end=... -> intervalul ca MP4 (max 30 min), prin
    coada de joburi: 202 + job, fisierul se descarca din /api/jobs/<id>/fisier.
    Local: taiere rapida din segmente (-c copy). NVR: pull RTSP tracks (dureaza ~cat clipul).
    &watermark=1 graveaza camera + ora in imagine (reencode, pentru probe)."""
    ch = int(request.args["channel"])
    start = request.args["start"]
    end = request.args["end"]
    t0, t1 = _ts(start), _ts(end)
    if not 0 < t1 - t0 <= 1800:
        return jsonify({"error": t("interval invalid (între 1 sec și 30 min)")}), 400
    resolve_camera(ch)
    return jsonify(jobs.submit("export", {"channel": ch, "start": start, "end": end,
                                          "watermark": bool(request.args.get("watermark"))},
                               who=_who(), cam=ch)), 202


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _job_export(job):
    p = job.parametri
    out = job.cale("mp4")
    ok, err = render_clip(p["channel"], p["start"], p["end"], out, wm=p.get("watermark"),
                          job=job)
    if not ok:
        raise jobs.JobError(err)
    job.fisier(out, f"cam{p['channel']}_{p['start']}.mp4", "video/mp4")
    job.progres(97, t("calculez SHA-256"))
    # hash-ul FISIERULUI LIVRAT, nu al unei regenerari: fisa probatorie il refoloseste
    sha = _sha256(out)
    try:   # registrul exporturilor: verificarea publica (/verifica) il regaseste dupa amprenta
        import verificare
        verificare.inregistreaza(sha, os.path.getsize(out), p["channel"], p["start"], p["end"],
                                 fel="export", cine=job.creat_de, nume=f"cam{p['channel']}_{p['start']}.mp4")
    except Exception as e:
        logare.info(f"[export] registru: {str(e)[:60]}")
    return {"octeti": os.path.getsize(out), "sha256": sha,
            "goluri": [[int(a), int(b)] for a, b in record.goluri(p["channel"], _ts(p["start"]), _ts(p["end"]))]}


def _export_livrat(ch, start, end):
    """Exportul recent (job gata, fisier inca pe disc, fara filigran) cu aceiasi parametri —
    fisa se face pe el, exact fisierul pe care l-a descarcat omul."""
    for j in jobs.lista(limit=200):
        if (j.get("tip") == "export" and j.get("stare") == "gata" and j.get("are_fisier")
                and (j.get("parametri") or {}).get("channel") == ch
                and (j.get("parametri") or {}).get("start") == start
                and (j.get("parametri") or {}).get("end") == end
                and not (j.get("parametri") or {}).get("watermark")):
            f = jobs.cale_fisier(j["id"])
            if f and os.path.exists(f[0]):
                return j, f[0]
    return None, None


jobs.register("clip", _job_clip)
jobs.register("chenare", _job_chenare)
jobs.register("raport", _job_raport)
jobs.register("export", _job_export)
