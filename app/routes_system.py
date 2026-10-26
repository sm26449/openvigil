#!/usr/bin/env python3
"""Blueprint: stare & configurare sistem — monitorizare, spatiu, setari, unelte.

Grup extras din server.py (audit #8): events/pulse (starea live), links, keyframes,
tts, ora camerelor, continuity/retention/storage (dovada ca inregistram si cat incape),
zgomot, system (CPU/GPU/subsisteme AI), speedtest, jurnal de modificari, backup/restore,
settings + vizualizari. Timerele de fundal (daily_time_check, enforce_stream_settings)
si _noise_event RAMAN in server — le porneste main(), nu sunt rute.
"""
import json
import os
import shutil
import subprocess
import time

from flask import Blueprint, Response, jsonify, request, send_file

import ai_client
import audio_level
import audit
import drivers
import backup
import camctl
import config as C
import continuity
import describe
import detect
import faces
import health
import mqtt as mqtt_bridge
import noise_stats
import notify
import plates
import record
import reencode
import rules
import semantic
import settings
from i18n import t
from server import (ROOT, _visible_ids, current_user, get_cameras, init_ext,
                    invalidate_cam_cache, load_ext, reload_streams)
import users

bp = Blueprint("system", __name__)


def _detect_state():
    """Starea detectiei (+ gate_ratio + stationare): din nvr-ai cand bucla ruleaza
    acolo (#9 pas 2), altfel din procesul local. La esec de transport, starea
    locala (goala cand bucla e remote) — badge-urile dispar pana revine serviciul,
    nimic nu crapa."""
    if ai_client.remote():
        d = ai_client.detect_state()
        if d is not None:
            return d
    import stationar
    return {"detect": detect.snapshot(), "gate_ratio": detect.gate_ratio(),
            "stationare": stationar.snapshot()}


def _detect_snapshot():
    return _detect_state()["detect"]


@bp.route("/api/events")
def api_events():
    """Alarme + detectii AI. Implicit trimitem DOAR starile active (cateva sute de octeti);
    istoricul recent se cere explicit cu ?full=1 — altfel, la 100 de camere, am trimite
    zeci de KB la fiecare 3 secunde degeaba."""
    alarms, ds = camctl.events_snapshot(), _detect_state()
    det = ds["detect"]
    vis = _visible_ids()
    if vis is not None:
        alarms = {k: v for k, v in alarms.items() if int(k) in vis}
        det = {k: v for k, v in det.items() if int(k) in vis}
    if request.args.get("full"):
        stat = {k: v for k, v in (ds.get("stationare") or {}).items()
                if vis is None or int(k) in vis}
        return jsonify({"alarms": alarms, "detect": det, "stationare": stat})
    return jsonify({
        "alarms": {k: {"active": v.get("active", [])} for k, v in alarms.items()},
        "detect": {k: {"labels": v.get("labels", [])} for k, v in det.items()},
    })


@bp.route("/api/links")
def api_links():
    """Legaturi rapide pentru UI: unelte locale + interfetele echipamentelor."""
    host = request.host.split(":")[0]
    return jsonify({
        "go2rtc": f"http://{host}:{getattr(C, 'STREAM_PORT', 8076)}",   # prin releu
        "nvr": None if settings.nvr_retras() else f"http://{C.NVR_IP}",
        "cameras": [{"id": c["id"], "name": c.get("name"), "url": f"http://{c['ip']}"}
                    for c in load_ext()],
    })


@bp.route("/api/keyframes", methods=["GET", "POST"])
def api_keyframes():
    """Intervalul dintre cadrele-cheie pe SUBSTREAM (fluxul din grila si din tab-ul live).
    Mai mic = grila porneste si se reconecteaza mai repede; costul in bitrate e neglijabil
    pentru ca substreamul e mic. POST {ms: 500..4000} il aplica pe toate camerele."""
    cams = load_ext()
    if request.method == "POST":
        ms = int((request.get_json(silent=True) or {}).get("ms") or 1000)
        ms = max(500, min(4000, ms))
        okn, errs = 0, []
        for c in cams:
            if not drivers.este_hik(c):
                continue                    # ONVIF: calitatea se seteaza din interfata camerei
            try:
                camctl.set_stream(c, 102, keyframe=ms)
                # ca la bitrate: NVR-ul re-impinge configuratia peste camerele proxied
                if c.get("nvr_channel") and not settings.nvr_retras():
                    camctl.set_stream({"ip": C.NVR_IP, "user": C.NVR_USER,
                                       "pass": C.NVR_PASS},
                                      c["nvr_channel"] * 100 + 2, keyframe=ms)
                okn += 1
            except Exception as e:
                errs.append(f"{c['id']}: {str(e)[:40]}")
            time.sleep(0.4)  # ritm lent: camerele blocheaza IP-ul la cereri prea dese
        return jsonify({"ok": True, "applied": okn, "total": len(cams), "errors": errs})
    vals = {}
    for c in cams:
        try:
            vals[c["id"]] = drivers.get_stream(c, 102).get("keyframe")
        except Exception:
            vals[c["id"]] = None
    seen = [v for v in vals.values() if v]
    return jsonify({"per_cam": vals,
                    "common": max(set(seen), key=seen.count) if seen else None})


@bp.route("/api/tts", methods=["POST"])
def api_tts():
    """Body: {text, speed?}. Genereaza un anunt vocal in romana, in formatul cerut de
    camera (WAV PCM 8 kHz mono). Intoarce fisierul: il asculti in pagina, apoi il incarci
    o singura data in camera (interfata ei) intr-unul din cele 3 sloturi."""
    b = request.get_json(silent=True) or {}
    text = (b.get("text") or "").strip()[:300]
    if not text:
        return jsonify({"error": t("scrie textul anunțului")}), 400
    speed = max(80, min(220, int(b.get("speed") or 150)))
    os.makedirs(os.path.join(ROOT, "data", "tmp"), exist_ok=True)
    tmp_raw = os.path.join(ROOT, "data", "tmp", "tts_raw.wav")
    out = os.path.join(ROOT, "data", "tmp", "tts.wav")
    try:
        subprocess.run(["espeak-ng", "-v", "ro", "-s", str(speed), "-p", "40",
                        "-w", tmp_raw, "--", text], capture_output=True, timeout=30, check=True)
        # formatul acceptat de camerele Hikvision pentru sunete proprii
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", tmp_raw,
                        "-ar", "8000", "-ac", "1", "-acodec", "pcm_s16le", out],
                       capture_output=True, timeout=30, check=True)
    except FileNotFoundError:
        return jsonify({"error": t("sinteza vocala nu e instalata pe server")}), 500
    except subprocess.CalledProcessError as e:
        return jsonify({"error": e.stderr.decode(errors="ignore")[-160:]}), 500
    return send_file(out, mimetype="audio/wav", as_attachment=bool(b.get("download")),
                     download_name="anunt.wav", max_age=0)


_pulse_cache = {"t": 0, "data": None}


@bp.route("/api/time")
def api_time():
    """Ora fiecarei camere si abaterea fata de ceasul serverului."""
    import concurrent.futures as cf
    def one(c):
        try:
            return {"id": c["id"], "name": c.get("name"), **drivers.get_time(c)}
        except Exception as e:
            return {"id": c["id"], "name": c.get("name"), "error": str(e)[:60]}
    with cf.ThreadPoolExecutor(8) as ex:
        rows = sorted(ex.map(one, load_ext()), key=lambda r: r["id"])
    import datetime
    return jsonify({"cameras": rows,
                    "server": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "ntp": settings.get("ntp_server")})


@bp.route("/api/time/sync", methods=["POST"])
def api_time_sync():
    """Sincronizeaza ora pe camerele date (implicit toate): fus luat din ceasul serverului
    (deci si ora de vara se rezolva la fiecare sincronizare), ora exacta, apoi NTP."""
    b = request.get_json(silent=True) or {}
    if b.get("ntp") is not None:
        settings.update({"ntp_server": (b["ntp"] or "").strip()[:60]})
        audit.log("server NTP", settings.get("ntp_server"))
    host = settings.get("ntp_server") or None
    cams = load_ext()
    ids = set(int(i) for i in (b.get("ids") or [])) or {c["id"] for c in cams}
    out = []
    for c in cams:
        if c["id"] not in ids:
            continue
        try:
            tz = drivers.set_time(c, ntp_host=host, use_ntp=bool(host))
            out.append({"id": c["id"], "ok": True, "tz": tz})
        except Exception as e:
            out.append({"id": c["id"], "ok": False, "err": str(e)[:60]})
        time.sleep(0.3)
    return jsonify({"ok": True, "reusite": sum(1 for r in out if r["ok"]),
                    "total": len(out), "results": out})


@bp.route("/api/continuity")
def api_continuity():
    """Dovada ca inregistram: cine scrie acum, acoperirea pe zile, si daca retentia incape."""
    days = min(60, max(1, request.args.get("days", 14, type=int)))
    azi = time.strftime("%Y-%m-%d")
    goluri = {}
    for cam in load_ext():
        if cam.get("record", "continuous") != "continuous":
            continue
        g = continuity.gaps(cam["id"], azi)
        if g:
            goluri[cam["id"]] = [{"de_la": time.strftime("%H:%M", time.localtime(x["start"])),
                                  "pana_la": time.strftime("%H:%M", time.localtime(x["end"])),
                                  "minute": round(x["sec"] / 60)} for x in g]
    return jsonify({"acum": continuity.live(), "jurnal": continuity.journal(days=days),
                    "goluri_azi": goluri, "spatiu": continuity.forecast(),
                    "probleme": continuity.problems()})


_spatiu_memo = {"t": 0, "v": {}}


def spatiu_per_camera(ttl=60):
    """Cat ocupa si de cate zile are material fiecare camera.

    Costa vreo 36.000 de interogari de fisiere (18 camere x ~2000 de segmente), deci
    raspunsul se refoloseste un minut. Inainte se recalcula la fiecare cerere si pagina de
    setari se bloca peste o secunda de fiecare data cand o deschideai."""
    if time.time() - _spatiu_memo["t"] < ttl:
        return _spatiu_memo["v"]
    out = {}
    for name in (os.listdir(record.REC_DIR) if os.path.isdir(record.REC_DIR) else []):
        if not name.startswith("cam"):
            continue
        cid = int(name[3:])
        files = record._files(cid)
        if not files:
            continue
        out[cid] = {"octeti": sum(os.path.getsize(p) for _, p, _ in files),
                    "zile": round((time.time() - files[0][0]) / 86400, 1),
                    "segmente": len(files)}
    _spatiu_memo.update(t=time.time(), v=out)
    return out


@bp.route("/api/retention")
def api_retention():
    """Cat istoric avem efectiv per camera si cat ocupa — ca sa vezi daca setarile de
    retentie sunt realiste pentru spatiul disponibil."""
    sp = spatiu_per_camera()
    out = []
    for c in get_cameras():
        d = sp.get(c["id"])
        if not d:
            continue
        out.append({"id": c["id"], "name": c["name"], "gb": round(d["octeti"] / 2**30, 1),
                    "zile": d["zile"], "segmente": d["segmente"],
                    "retention": c.get("retention")})
    tot = sum(r["gb"] for r in out)
    s = settings.load()
    # nivelul de arhiva: exista? cat ocupa? cat e liber acolo?
    ad = record.archive_dir()
    arhiva = {"activ": bool(ad), "dir": ad or "", "exista": bool(ad and os.path.isdir(ad)),
              "scriibil": bool(ad and os.access(ad, os.W_OK)), "gb": 0.0, "segmente": 0, "liber_gb": None}
    if arhiva["exista"]:
        try:
            for n in os.listdir(ad):
                if n.startswith("cam") and n[3:].isdigit():
                    for t, p, fn in record._listeaza(ad, int(n[3:])):
                        arhiva["segmente"] += 1
                        arhiva["gb"] += os.path.getsize(p) / 2**30
            arhiva["gb"] = round(arhiva["gb"], 1)
            arhiva["liber_gb"] = round(shutil.disk_usage(ad).free / 2**30, 1)
        except OSError:
            pass
    return jsonify({"camere": out, "total_gb": round(tot, 1), "arhiva": arhiva,
                    "setari": {k: s[k] for k in
                               ("retention_days", "retention_event_days",
                                "retention_snapshots_days", "retention_min_free_gb",
                                "events_keep_days", "arhiva_dir", "arhiva_zile",
                                "arhiva_min_free_gb")}})


@bp.route("/api/noise/export.csv")
def api_noise_csv():
    """Orele masurate, ca CSV — pentru cine vrea sa le rasuceasca in altceva."""
    days = min(30, max(1, request.args.get("days", 7, type=int)))
    vis = _visible_ids()
    linii = ["camera,nume,ora,esantioane,leq_db,min_db,max_db,l90_db,mediana_db,l10_db,p99_db"]
    for c in load_ext():
        if c["id"] not in audio_level.monitors or (vis is not None and c["id"] not in vis):
            continue
        nume = c["name"].replace(",", " ")
        for r_ in noise_stats.hourly(c["id"], days):
            linii.append(f'{c["id"]},{nume},{r_["h"]},{r_["n"]},{r_["leq"]},{r_["min"]},'
                         f'{r_["max"]},{r_["l90"]},{r_["med"]},{r_["l10"]},{r_["p99"]}')
    return Response("\n".join(linii) + "\n", mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=zgomot-{time.strftime('%Y%m%d')}.csv"})


@bp.route("/api/noise/stats")
def api_noise_stats():
    """Istoricul orar + profilul pe ora din zi, pentru calibrarea pragurilor."""
    days = min(30, max(1, request.args.get("days", 7, type=int)))
    vis = _visible_ids()
    cams = [c for c in load_ext() if c["id"] in audio_level.monitors
            and (vis is None or c["id"] in vis)]
    return jsonify({"zile": days, "camere": [
        {"id": c["id"], "name": c["name"],
         "profil": noise_stats.profile(c["id"], days),
         "ore": noise_stats.hourly(c["id"], days),
         "propunere": noise_stats.suggest(c["id"], days)} for c in cams]})


@bp.route("/api/noise")
def api_noise():
    """Nivelul de zgomot masurat pe camerele cu microfon + istoricul recent."""
    minutes = int(request.args.get("minutes") or 15)
    cur = audio_level.snapshot()
    vis = _visible_ids()
    out = []
    for c in get_cameras():
        if c["id"] not in cur or (vis is not None and c["id"] not in vis):
            continue
        m = audio_level.monitors[c["id"]]
        out.append({"id": c["id"], "name": c["name"], **cur[c["id"]],
                    "serie": m.series(minutes)})
    s = settings.load()
    return jsonify({"camere": out, "reguli": {k: s[k] for k in
                    ("noise_alarm_enabled", "noise_threshold_db",
                     "noise_sustain_sec", "noise_spike_db", "incident_gap_sec")}})


def _gpu_mem():
    """VRAM: folosit / liber / total (MiB) + procent — pentru a vedea cat de aproape de
    OOM suntem. `free` din mem_get_info e VRAM-ul liber real pe placa (tot sistemul)."""
    try:
        import torch
        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        return {"folosit_mb": round((total - free) / 2**20),
                "liber_mb": round(free / 2**20),
                "total_mb": round(total / 2**20),
                "procent": round((total - free) / total * 100)}
    except Exception:
        return None


@bp.route("/api/system")
def api_system():
    """Starea serverului: procesor, memorie, disc, trafic de retea + subsistemele AI."""
    st = health.system_status(record.REC_DIR)
    st["inregistrari_blocate"] = record.blocat()   # volum/permisiuni/cheie lipsa
    s = settings.load()
    # pe unde pot pleca alertele: fara niciun canal, totul ramane doar in jurnal (patit: 2026-10-25)
    try:
        import push
        n_push = len(push.lista())
    except Exception:
        n_push = 0
    st["notificari"] = {"telegram": bool(s.get("telegram_token") and s.get("telegram_chat")),
                        "email": bool(s.get("email_enabled") and s.get("email_smtp") and s.get("email_to")),
                        "push": n_push}
    st["notificari"]["canal"] = st["notificari"]["telegram"] or st["notificari"]["email"] or n_push > 0
    # NVR-ul Hikvision: istoricul vechi si exportul fara inregistrari locale trec prin el —
    # cand pica (2026-10-25, 01:37), omul afla doar din Telegram; aici se vede si pe ecran
    retras = settings.nvr_retras()
    st["nvr"] = {"ip": C.NVR_IP, "retras": retras, "online": None if retras else notify.stare_nvr()}
    # cand modelele traiesc in nvr-ai (#9 pas 1), starea "incarcat" vine de acolo —
    # atributele locale ar minti (in web raman doar fallback-uri lazy, descarcate)
    caps = (ai_client.capabilities() or {}).get("modele") if ai_client.remote() else None
    dstate = ai_client.detect_state() if caps else None
    m = (lambda k, local: caps[k]["incarcat"] if caps and k in caps else local)
    st["ai"] = {
        "gpu": _gpu_mem(),
        "serviciu": "nvr-ai" if caps else "local",
        "face": {"pornit": s["face_enabled"], "model": m("face", faces._app is not None)},
        "lpr": {"pornit": s["lpr_enabled"], "model": m("lpr", plates._alpr is not None)},
        "descriere": {"pornit": s["person_describe"],
                      "model": m("describe", describe._model is not None)},
        "semantic": {"pornit": s["semantic_enabled"],
                     "model": m("semantic", semantic._model is not None),
                     "backfill": (caps["semantic"]["backfill"]
                                  if caps and "semantic" in caps
                                  else dict(semantic.backfill_state))},
        "mqtt": {"pornit": bool(s["mqtt_enabled"]), **{k: v for k, v in
                 mqtt_bridge.state.items() if k in ("connected", "broker", "published")}},
        "reguli": len([r for r in rules.load() if r.get("activ", True)]),
        "motion_gate": {"pornit": bool(s.get("motion_gate")),
                        "sarite": dstate["gate_ratio"] if dstate else detect.gate_ratio()},
    }
    if caps:
        st["ai"]["metrici"] = ai_client.metrici()   # cine munceste si cat de repede
    return jsonify(st)


# blob incompresibil generat o data (zerourile s-ar comprima -> masuratoare falsa).
# 16MB in RAM, feliat dupa cerere.
_SPEEDTEST_BLOB = os.urandom(16 * 1024 * 1024)


@bp.route("/api/speedtest")
def api_speedtest():
    """Descarca ~N MB pentru masurarea vitezei server->client (bandwidth-ul real, pe reteaua
    curenta a clientului). ?mb=1..16 (implicit 4). Fara cache -> timing real. Datele sunt
    aleatoare (incompresibile), ca un gzip pe drum sa nu falsifice masuratoarea."""
    mb = min(16, max(1, request.args.get("mb", 4, type=int)))
    resp = Response(_SPEEDTEST_BLOB[:mb * 1024 * 1024],
                              mimetype="application/octet-stream")
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["Content-Length"] = str(mb * 1024 * 1024)
    return resp


@bp.route("/api/changes")
def api_changes():
    return jsonify(audit.tail(int(request.args.get("n") or 150),
                              request.args.get("q") or ""))


@bp.route("/api/backup")
def api_backup():
    """Descarca configuratia. ?fara_parole=1 pentru o copie fara date sensibile."""
    data = backup.export_config(include_secrets=not request.args.get("fara_parole"))
    audit.log("export configurație", detail={"fara_parole": data["fara_parole"]})
    resp = Response(json.dumps(data, indent=2, ensure_ascii=False),
                              mimetype="application/json")
    resp.headers["Content-Disposition"] = (
        f'attachment; filename="nvr-config-{time.strftime("%Y%m%d")}.json"')
    return resp


@bp.route("/api/restore", methods=["POST"])
def api_restore():
    """Reface configuratia dintr-un fisier exportat (starea curenta se pastreaza ca .inainte-de-*)."""
    try:
        restored = backup.import_config(request.get_json(force=True, silent=False))
    except Exception as e:
        return jsonify({"error": str(e)[:120]}), 400
    audit.log("refacere configurație", detail={"sectiuni": restored})
    invalidate_cam_cache()
    init_ext()
    reload_streams()
    detect.sync()
    return jsonify({"ok": True, "sectiuni": restored,
                    "nota": t("repornește serviciile dacă ceva nu se comporta corect")})


@bp.route("/api/pulse")
def api_pulse():
    """Starea pe scurt a fiecarei camere, pentru banda din antet: inregistreaza acum?
    are alarma activa? cand a fost ultimul eveniment? — un singur apel, ieftin."""
    now = time.time()
    if _pulse_cache["data"] and now - _pulse_cache["t"] < 4:
        return jsonify(_pulse_cache["data"])   # scanarea directoarelor e scumpa la multe camere
    alarms = camctl.events_snapshot()
    det = _detect_snapshot()
    out = []
    for c in get_cameras():
        cid = c["id"]
        files = record._files(cid)
        last_write = files[-1][0] + record.SEGMENT if files else 0
        a = alarms.get(str(cid)) or alarms.get(cid) or {}
        d = det.get(cid) or {}
        out.append({
            "id": cid, "name": c["name"],
            "rec": bool(files) and (now - files[-1][0]) < record.SEGMENT * 2,
            "mode": c.get("record", "continuous"),
            "alarm": bool(a.get("active")),
            "detect": bool(d.get("labels")),
            "age": int(now - last_write) if last_write else None,
        })
    _pulse_cache.update(t=now, data=out)
    vis = _visible_ids()
    if vis is not None:
        return jsonify([r for r in out if r["id"] in vis])
    return jsonify(out)


@bp.route("/api/storage")
def api_storage():
    """Sumar spatiu: cat ocupa inregistrarile, cat e liber, ritmul si retentia estimata."""
    import shutil as sh
    du = sh.disk_usage(os.path.join(ROOT, "data"))
    sp = spatiu_per_camera()
    per_cam = {cid: round(d["octeti"] / 2**30, 1) for cid, d in sp.items()}
    total = sum(d["octeti"] for d in sp.values())
    # ritm: din bitrate-urile configurate ale camerelor care inregistreaza
    return jsonify({
        "used_gb": round(total / 2**30, 1),
        "free_gb": round(du.free / 2**30, 1),
        "per_cam_gb": per_cam,
        "reencode": {**reencode.state, "available": bool(reencode.available_encoder())},
    })


@bp.route("/api/settings", methods=["GET", "POST"])
def api_settings():
    # parola veche unica a fost inlocuita de conturi (data/users.json); campul
    # web_password ramane in settings doar ca relicva si nu paraseste serverul
    if request.method == "GET":
        s = settings.load()
        s.pop("web_password", None)
        return jsonify(s)
    b = request.get_json(silent=True) or {}
    b.pop("web_password", None)
    b.pop("web_password_new", None)
    s = settings.update(b)
    if any(k.startswith("mqtt_") for k in b):
        mqtt_bridge.reconfigure()   # conexiunea se reface cu setarile noi
    if "ai_service" in b:
        # comutatorul decide UNDE ruleaza buclele: local se opresc/pornesc pe loc
        # (nvr-ai isi vede singur setarea, prin mtime)
        import track
        detect.sync()
        track.sync()
    s.pop("web_password", None)
    return jsonify(s)


@bp.route("/api/views", methods=["GET", "POST", "DELETE"])
def api_views():
    """Vizualizari salvate: o combinatie de grup, cautare, densitate si mod de afisare,
    ca sa nu refaci filtrele de fiecare data. POST adauga/inlocuieste dupa nume."""
    views = settings.get("views")
    if request.method == "GET":
        return jsonify({"views": views, "default": settings.get("default_view")})
    b = request.get_json(silent=True) or {}
    name = (b.get("name") or "").strip()[:40]
    if not name:
        return jsonify({"error": t("vizualizarea are nevoie de un nume")}), 400
    views = [v for v in views if v.get("name") != name]
    if request.method == "POST":
        views.append({"name": name, "group": b.get("group") or "",
                      "q": (b.get("q") or "")[:60], "cols": b.get("cols") or "auto",
                      "preview": bool(b.get("preview")), "ids": b.get("ids") or None})
        upd = {"views": views}
        if b.get("make_default"):
            upd["default_view"] = name
        settings.update(upd)
    else:
        upd = {"views": views}
        if settings.get("default_view") == name:
            upd["default_view"] = ""
        settings.update(upd)
    return jsonify({"ok": True, "views": settings.get("views"),
                    "default": settings.get("default_view")})


@bp.route("/api/views/default", methods=["POST"])
def api_view_default():
    name = (request.get_json(silent=True) or {}).get("name") or ""
    settings.update({"default_view": name})
    return jsonify({"ok": True, "default": name})


@bp.route("/api/settings/test/<channel>", methods=["POST"])
def api_settings_test(channel):
    """Trimite un mesaj de test pe telegram/email cu setarile CURENTE (salveaza intai)."""
    b = request.get_json(silent=True) or {}
    if b:
        settings.update(b)
    try:
        notify.test_channel(channel)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 502
