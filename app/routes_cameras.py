#!/usr/bin/env python3
"""Blueprint: camere — CRUD, actiuni (sirena/strobo/say/volum), PTZ, stream, config.

Grupul 'camere' complet (audit #8): CRUD + discover/import, actiunile live, calitatea
fluxului, config, bulk, health/reprobe/reboot si controlul PTZ. Helper-urile partajate
vin din `server`; invalidarea cache-ului de camere se face prin invalidate_cam_cache()
(un `global _cams` aici ar atinge modulul acesta, nu server-ul).
"""
import os
import subprocess
import time
import xml.etree.ElementTree as ET

import requests
from flask import Blueprint, jsonify, request
from requests.auth import HTTPDigestAuth

import audit
import camctl
import config as C
import detect
import discover
import drivers
import health
from i18n import t
import jobs
import record
import settings
from server import (EXT_BASE_ID, ROOT, _cam_entry, _remember_desired,
                    _set_stream_synced, _visible_ids, current_user, get_cameras,
                    invalidate_cam_cache, load_ext, next_cam_id, reload_streams, save_ext,
                    strip_ns, sync_all_watchers, xml_text, EXT_LOCK)
import logare

bp = Blueprint("cameras", __name__)


@bp.route("/api/cam/<int:cam_id>/ptz", methods=["POST"])
def api_ptz(cam_id):
    """Body: {pan, tilt, zoom} intre -100 si 100; toate zero = stop."""
    cam = _cam_entry(cam_id)
    f = (cam or {}).get("features") or {}
    if not cam or not (f.get("zoom") or f.get("ptz")):
        return jsonify({"error": t("camera nu are lentila motorizată sau cap mobil")}), 404
    b = request.get_json(silent=True) or {}
    pan, tilt = int(b.get("pan") or 0), int(b.get("tilt") or 0)
    if (pan or tilt) and not f.get("ptz"):
        return jsonify({"error": t("camera nu se rotește")}), 400
    try:
        drivers.ptz_move(cam, pan=pan, tilt=tilt, zoom=int(b.get("zoom") or 0))
        return jsonify({"ok": True})
    except (requests.RequestException, RuntimeError) as e:
        return jsonify({"error": str(e)}), 502


@bp.route("/api/cam/<int:cam_id>/presets", methods=["GET", "POST", "DELETE"])
def api_presets(cam_id):
    """Pozitiile memorate in camera. GET = lista, POST {nume} = salveaza pozitia curenta
    intr-un slot liber, POST {id} = du-te acolo, DELETE ?id= = sterge."""
    cam = _cam_entry(cam_id)
    if not cam or not (cam.get("features") or {}).get("presets"):
        return jsonify({"error": t("camera nu memorează poziții")}), 404
    try:
        if request.method == "GET":
            return jsonify(drivers.presets_list(cam))
        if request.method == "DELETE":
            drivers.preset_delete(cam, request.args.get("id", type=int))
            audit.log("presetare ștearsă", f"cam{cam_id}", {"id": request.args.get("id")})
            return jsonify({"ok": True})
        b = request.get_json(silent=True) or {}
        if b.get("id") is not None and not b.get("nume"):
            drivers.preset_goto(cam, int(b["id"]))
            return jsonify({"ok": True})
        if not drivers.este_hik(cam):
            tok = drivers.preset_save(cam, None, b.get("nume") or t("Pozitie"))
            audit.log("presetare salvată", f"cam{cam_id}", {"token": tok, "nume": b.get("nume")})
            return jsonify({"ok": True, "token": tok})
        folosite = {p["id"] for p in drivers.presets_list(cam)}
        pid = next((i for i in range(1, 200) if i not in folosite), None)
        if pid is None:
            return jsonify({"error": t("nu mai sunt sloturi libere")}), 400
        drivers.preset_save(cam, pid, b.get("nume") or t("Pozitia {id}", id=pid))
        audit.log("presetare salvată", f"cam{cam_id}", {"id": pid, "nume": b.get("nume")})
        return jsonify({"ok": True, "id": pid})
    except (requests.RequestException, RuntimeError) as e:
        return jsonify({"error": str(e)}), 502


@bp.route("/api/cam/<int:cam_id>/focus", methods=["GET", "POST"])
def api_focus(cam_id):
    """Modul de focalizare: AUTO / SEMIAUTO / MANUAL."""
    cam = _cam_entry(cam_id)
    if not cam:
        return jsonify({"error": t("camera inexistenta")}), 404
    if not drivers.este_hik(cam):
        return jsonify({"error": t("focalizarea se seteaza doar pe camerele Hikvision")}), 400
    try:
        if request.method == "GET":
            return jsonify({"mod": camctl.get_focus_mode(cam), "optiuni": camctl.FOCUS_MODES})
        mod = (request.get_json(silent=True) or {}).get("mod")
        nou = camctl.set_focus_mode(cam, mod)
        audit.log("focalizare", f"cam{cam_id}", {"mod": nou})
        return jsonify({"ok": True, "mod": nou})
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502


@bp.route("/api/cam/<int:cam_id>/detect-ptz", methods=["POST"])
def api_detect_ptz(cam_id):
    """Verifica prin proba daca respectiva camera chiar se roteste, si retine raspunsul.
    Nu ne putem lua dupa capabilitatile raportate: si bullet-urile fixe le anunta."""
    cam = _cam_entry(cam_id)
    if not cam:
        return jsonify({"error": t("camera inexistenta")}), 404

    def frame():
        r = requests.get(f"{C.GO2RTC_API}/api/frame.jpeg",
                         params={"src": f"cam{cam_id}"}, timeout=15)
        r.raise_for_status()
        return r.content

    if not drivers.este_hik(cam):
        return jsonify({"error": t("proba de rotire merge doar pe camerele Hikvision; pe ONVIF folosește butoanele PTZ direct")}), 400
    rez = camctl.detect_ptz(cam, frame)
    with EXT_LOCK:
        cams = load_ext()
        for c in cams:
            if c["id"] == cam_id:
                c.setdefault("features", {}).update(rez)
        save_ext(cams)
    audit.log("proba de rotire", f"cam{cam_id}", rez)
    return jsonify(rez)


@bp.route("/api/cam/<int:cam_id>/zone", methods=["POST"])
def api_zone(cam_id):
    """Body: {points: [[x,y],...]} normalizat 0..1 (minim 3 puncte) sau {points: null}."""
    if not any(c["id"] == cam_id for c in get_cameras()):
        return jsonify({"error": t("camera nu există")}), 404
    pts = (request.get_json(silent=True) or {}).get("points")
    if pts is not None and (not isinstance(pts, list) or len(pts) < 3):
        return jsonify({"error": t("minim 3 puncte")}), 400
    detect.set_zone(cam_id, pts)
    return jsonify({"ok": True})


@bp.route("/api/cam/<int:cam_id>/masca", methods=["POST"])
def api_masca(cam_id):
    """Masca de confidentialitate: {rects: [[x,y,w,h],...]} normalizat sau {rects: null}.
    Se aplica pe live/HD/redare (template-uri go2rtc regenerate), clipuri si exporturi."""
    import masca
    with EXT_LOCK:
        cams = load_ext()
        cam = next((c for c in cams if c["id"] == cam_id), None)
        if not cam:
            return jsonify({"error": t("camera nu există")}), 404
        try:
            rects = masca.normalizeaza((request.get_json(silent=True) or {}).get("rects"))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if rects:
            cam["mask"] = rects
        else:
            cam.pop("mask", None)
        save_ext(cams)
    audit.log("masca de confidențialitate", cam.get("name") or cam_id,
              {"dreptunghiuri": len(rects or [])})
    invalidate_cam_cache()
    reload_streams()       # template-urile m<id> din go2rtc.yaml + reincarcare la cald
    return jsonify({"ok": True, "mask": rects})


@bp.route("/api/cam/<int:cam_id>/deploy-smart", methods=["POST"])
def api_deploy_smart(cam_id):
    """Trimite liniile/zonele configurate PE camera (ISAPI LineDetection/FieldDetection),
    ca sa le impuna la rezolutie nativa. Camera declanseaza, AI-ul confirma pe HD."""
    cam = _cam_entry(cam_id)
    if not cam:
        return jsonify({"error": t("camera nu există")}), 404
    if not cam.get("ip") or not cam.get("user"):
        return jsonify({"error": t("doar camere directe pe IP (nu prin NVR)")}), 400
    lines = (settings.get("count_lines") or {}).get(str(cam_id)) or []
    zones = (settings.get("count_zones") or {}).get(str(cam_id)) or []
    if not lines and not zones:
        return jsonify({"error": t("desenează întâi o linie sau o zona")}), 400
    try:
        res = camctl.deploy_smart(cam, lines, zones)
    except Exception as e:
        return jsonify({"error": t("deploy eșuat: {e}", e=str(e)[:120])}), 502
    audit.log("linii/zone trimise pe camera", cam_id)
    return jsonify({"ok": True, **res})


@bp.route("/api/cameras")
def api_cameras():
    try:
        on = detect.enabled()
        auto = detect.auto_siren_on()
        out = [{**c, "detect": c["id"] in on, "auto_siren": c["id"] in auto,
                "zone": detect.cam_cfg(c["id"]).get("zone"),
                "playback": c["playback"] or (c["source"] == "ext"
                                              and record.has_recordings(c["id"]))}
               for c in get_cameras()]
        u = current_user()
        if u and u["role"] != "admin":
            # viewerul/operatorul vede doar camerele lui, fara datele de conexiune.
            # Operatorul pastreaza capabilitatile intregi — altfel butoanele lui de
            # actiune (sirena, vorbit, PTZ) n-ar avea de unde sa stie ca exista.
            vis = _visible_ids()
            oper = u["role"] == "operator"
            out = [{"id": c["id"], "name": c["name"], "group": c["group"], "vendor": c.get("vendor"),
                    "source": c["source"], "playback": c["playback"],
                    "detect": c["detect"], "auto_siren": c.get("auto_siren"),
                    "features": (c["features"] if oper else
                                 {k: v for k, v in (c["features"] or {}).items()
                                  if k in ("mic", "aac")})}
                   for c in out if vis is None or c["id"] in vis]
        return jsonify(out)
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@bp.route("/api/cameras", methods=["POST"])
def api_add_camera():
    """Body JSON: {name, ip, port?, user, pass}. Verificarile (ISAPI, stocare, flux video,
    capabilitati) iau ~40 s, deci ruleaza ca job: 202 + jobul; rezultatul e camera.
    Parola nu se persista in job (merge prin `secret`, doar in memorie)."""
    b = request.get_json(force=True, silent=True) or {}
    ip = (b.get("ip") or "").strip()
    user = (b.get("user") or "").strip()
    pw = b.get("pass") or ""
    port = int(b.get("port") or 554)
    name = (b.get("name") or "").strip()
    vendor = b.get("vendor") if b.get("vendor") in drivers.VENDORI else "hik"
    if vendor == "demo":
        return jsonify({"error": t("camerele demo se adaugă din Setări → Unelte → Mod demo")}), 400
    if not (ip and user and pw):
        return jsonify({"error": t("ip, user și parola sunt obligatorii")}), 400
    if any(c["ip"] == ip for c in load_ext()):
        return jsonify({"error": t("camera cu IP {ip} există deja", ip=ip)}), 409
    return jsonify(jobs.submit("camera_noua", {"name": name, "ip": ip, "port": port,
                                               "user": user, "vendor": vendor,
                                               "http_port": int(b.get("http_port") or 80)},
                               who=(current_user() or {}).get("user"),
                               secret={"pass": pw})), 202


def _job_camera_noua(job):
    p = job.parametri
    ip, user, port, name = p["ip"], p["user"], p["port"], p["name"]
    pw = job.secret.get("pass") or ""
    cams = load_ext()
    if any(c["ip"] == ip for c in cams):
        raise jobs.JobError(t("camera cu IP {ip} există deja", ip=ip))
    if p.get("vendor") == "onvif":
        return _camera_noua_onvif(job, cams, ip, user, pw, port, name, p.get("http_port") or 80)

    # testeaza ISAPI cu credentialele date si citeste modelul
    job.progres(10, t("contactez camera"))
    sess = requests.Session()
    sess.auth = HTTPDigestAuth(user, pw)
    try:
        r = sess.get(f"http://{ip}/ISAPI/System/deviceInfo", timeout=12)
        if r.status_code == 401:
            raise jobs.JobError(t("autentificare esuata (user/parola)"))
        r.raise_for_status()
        info = ET.fromstring(r.text)
        model = xml_text(info, "model")
    except requests.ConnectionError:
        raise jobs.JobError(t("nu pot contacta camera: {ip} nu răspunde pe HTTP", ip=ip))
    except requests.RequestException as e:
        raise jobs.JobError(t("nu pot contacta camera: {e}", e=str(e)[:120]))

    # are stocare (SD/HDD) -> playback disponibil
    job.progres(30, t("verific stocarea"))
    playback = False
    try:
        hdd = ET.fromstring(sess.get(f"http://{ip}/ISAPI/ContentMgmt/Storage/hdd",
                                     timeout=12).text)
        playback = any(strip_ns(x.tag) == "hdd" for x in hdd.iter())
    except Exception:
        pass

    new_id = next_cam_id(cams)
    cam = {"id": new_id, "name": name or (model or f"Camera {new_id}"),
           "ip": ip, "port": port, "user": user, "pass": pw,
           "model": model, "playback": playback}
    # verificam ca fluxul video chiar functioneaza, nu doar ca raspunde camera
    job.progres(45, t("verific fluxul video"))
    hc = health.check_stream(cam, 102)
    if not hc.get("ok"):
        raise jobs.JobError(t("camera răspunde, dar nu livreaza imagine: {e}", e=hc.get("eroare")))
    # ce stie camera sa faca (mic/difuzor/sirena/stroboscop) + pregatire (audio AAC, linkage)
    job.progres(65, t("citesc capabilitatile"))
    cam["features"] = camctl.probe_features(cam)
    camctl.setup_camera(cam, cam["features"])
    job.progres(90, t("salvez și pornesc fluxurile"))
    with EXT_LOCK:
        cams = load_ext()                 # recitit: altcineva putea adauga intre timp
        if any(c["ip"] == ip for c in cams):
            raise jobs.JobError(t("camera cu IP {ip} există deja", ip=ip))
        cams.append(cam)
        save_ext(cams)   # nvr-record vede mtime-ul si porneste inregistrarea in <10s
    sync_all_watchers(cams)
    reload_streams()
    audit.log("camera adăugată", f"{cam['name']} ({ip})", who=job.creat_de)
    return {"id": new_id, "name": cam["name"], "model": model,
            "ip": ip, "source": "ext", "playback": playback,
            "features": cam["features"]}


def _camera_noua_onvif(job, cams, ip, user, pw, port, name, http_port):
    """Camera non-Hikvision: identificare + fluxuri prin ONVIF, apoi aceleasi verificari."""
    job.progres(10, t("contactez camera (ONVIF)"))
    cam = {"id": 0, "name": name, "ip": ip, "port": port, "user": user, "pass": pw,
           "vendor": "onvif", "onvif": {"port": int(http_port)}}
    try:
        info = drivers.device_info(cam)
        job.progres(25, f"{info.get('manufacturer') or ''} {info.get('model') or ''}".strip())
        cam["onvif"].update(drivers.descopera_fluxuri(cam))
    except Exception as e:
        raise jobs.JobError(t("ONVIF: {e}", e=str(e)[:120]))
    model = " ".join(x for x in (info.get("manufacturer"), info.get("model")) if x) or "ONVIF"
    new_id = next_cam_id(cams)
    cam.update({"id": new_id, "name": name or (info.get("model") or f"Camera {new_id}"),
                "model": model, "playback": False})
    job.progres(45, t("verific fluxul video"))
    hc = health.check_stream(cam, 102)
    if not hc.get("ok"):
        raise jobs.JobError(t("camera răspunde, dar nu livreaza imagine: {e}", e=hc.get("eroare")))
    job.progres(65, t("citesc capabilitatile"))
    cam["features"] = drivers.probe_features(cam)
    job.progres(90, t("salvez și pornesc fluxurile"))
    with EXT_LOCK:
        cams = load_ext()
        if any(c["ip"] == ip for c in cams):
            raise jobs.JobError(t("camera cu IP {ip} există deja", ip=ip))
        cams.append(cam)
        save_ext(cams)
    sync_all_watchers(cams)
    reload_streams()
    audit.log("camera adăugată (ONVIF)", f"{cam['name']} ({ip})", who=job.creat_de)
    return {"id": cam["id"], "name": cam["name"], "model": model, "ip": ip, "source": "ext",
            "playback": False, "vendor": "onvif", "features": cam["features"]}


jobs.register("camera_noua", _job_camera_noua)


DEMO_CAMERE = (("Intrare", "testsrc2"), ("Curte", "testsrc"), ("Garaj", "smptebars"), ("Stradă", "gradients"))


@bp.route("/api/demo/camere", methods=["POST"])
def api_demo_camere():
    """Mod demo: {actiune: "adauga"} pune 4 camere sintetice (go2rtc le genereaza imaginea,
    fara nicio camera reala); {actiune: "sterge"} le scoate. Pentru instalari goale, teste
    si capturi de ecran care nu arata casa nimanui."""
    b = request.get_json(silent=True) or {}
    actiune = b.get("actiune") or "adauga"
    with EXT_LOCK:
        cams = load_ext()
        if actiune == "sterge":
            rest = [c for c in cams if c.get("vendor") != "demo"]
            sterse = [c["id"] for c in cams if c.get("vendor") == "demo"]
            save_ext(rest)
            cams = rest
        else:
            sterse = []
            existente = {c.get("demo") for c in cams if c.get("vendor") == "demo"}
            for nume, tipar in DEMO_CAMERE:
                if tipar in existente:
                    continue
                nid = next_cam_id(cams)
                cams.append({"id": nid, "name": t("{nume} (demo)", nume=t(nume)), "ip": f"demo-{tipar}", "port": 0,
                             "user": "", "pass": "", "model": "Demo", "vendor": "demo", "demo": tipar,
                             "playback": False, "record": "continuous",
                             "features": drivers.probe_features({"vendor": "demo"})})
            save_ext(cams)
    invalidate_cam_cache()
    sync_all_watchers(cams)
    for cid in sterse:
        try:
            requests.delete(f"{C.GO2RTC_API}/api/streams", params={"src": f"cam{cid}"}, timeout=10)
        except Exception:
            pass
    reload_streams()
    audit.log(f"mod demo: {actiune} ({len([c for c in cams if c.get('vendor') == 'demo'])} camere demo)")
    return jsonify({"ok": True, "demo": [c["id"] for c in cams if c.get("vendor") == "demo"]})


@bp.route("/api/cameras/<int:cam_id>", methods=["DELETE"])
def api_del_camera(cam_id):
    with EXT_LOCK:
        cams = load_ext()
        rest = [c for c in cams if c["id"] != cam_id]
        if len(rest) == len(cams):
            return jsonify({"error": t("camera nu există")}), 404
        save_ext(rest)
    sync_all_watchers(rest)
    try:
        requests.delete(f"{C.GO2RTC_API}/api/streams", params={"src": f"cam{cam_id}"}, timeout=10)
    except Exception:
        pass
    reload_streams()
    return jsonify({"ok": True})


# ---- actiuni camere (sirena, stroboscop, releu, volum, zoom, evenimente) ----
# merg pe ORICE camera care isi declara capabilitatea (features), standalone sau NVR-direct

@bp.route("/api/cam/<int:cam_id>/siren", methods=["POST"])
def api_siren(cam_id):
    """Body optional: {sound: audioID, volume: 1..100, times: n}. Reda sunetul prin difuzor."""
    cam = _cam_entry(cam_id)
    if not cam or not cam.get("features", {}).get("siren"):
        return jsonify({"error": t("camera nu are sirena")}), 404
    b = request.get_json(silent=True) or {}
    try:
        drivers.trigger_siren(cam, b.get("sound"), b.get("volume"), b.get("times"),
                              custom=bool(b.get("custom")))
        return jsonify({"ok": True})
    except (requests.RequestException, RuntimeError) as e:
        return jsonify({"error": str(e)}), 502


@bp.route("/api/cam/<int:cam_id>/strobe", methods=["POST"])
def api_strobe(cam_id):
    """Body: {on: true/false}. Siguranta: se stinge singur dupa 60s."""
    cam = _cam_entry(cam_id)
    if not cam or not cam.get("features", {}).get("strobe"):
        return jsonify({"error": t("camera nu are stroboscop")}), 404
    b = request.get_json(silent=True) or {}
    try:
        drivers.set_strobe(cam, bool(b.get("on")))
        return jsonify({"ok": True, "on": bool(b.get("on"))})
    except (requests.RequestException, RuntimeError) as e:
        return jsonify({"error": str(e)}), 502


@bp.route("/api/cam/<int:cam_id>/output", methods=["POST"])
def api_output(cam_id):
    """Body: {on: true/false, port: 1}. Comuta releul (iesirea de alarma)."""
    cam = _cam_entry(cam_id)
    if not cam or not cam.get("features", {}).get("io_out"):
        return jsonify({"error": t("camera nu are ieșire de alarma")}), 404
    b = request.get_json(silent=True) or {}
    try:
        drivers.set_output(cam, int(b.get("port") or 1), bool(b.get("on")))
        return jsonify({"ok": True})
    except (requests.RequestException, RuntimeError) as e:
        return jsonify({"error": str(e)}), 502

@bp.route("/api/cam/<int:cam_id>/say", methods=["POST"])
def api_say(cam_id):
    """Body: {text} — rosteste un mesaj prin difuzorul/iesirea audio a camerei
    (canalul de vorbire, altul decat sirena). Sau {tone: true} pentru un ton de test."""
    cam = _cam_entry(cam_id)
    if not cam or not cam.get("features", {}).get("talk"):
        return jsonify({"error": t("camera nu are canal de vorbire")}), 404
    b = request.get_json(silent=True) or {}
    os.makedirs(os.path.join(ROOT, "data", "tmp"), exist_ok=True)
    wav = os.path.join(ROOT, "data", "tmp", f"say{cam_id}.wav")
    try:
        if b.get("tone"):
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-f", "lavfi", "-i", "sine=frequency=880:duration=6",
                            "-ar", "8000", "-ac", "1", wav], check=True, timeout=30)
        else:
            text = (b.get("text") or "").strip()[:300]
            if not text:
                return jsonify({"error": t("scrie mesajul")}), 400
            raw = os.path.join(ROOT, "data", "tmp", f"say{cam_id}_raw.wav")
            subprocess.run(["espeak-ng", "-v", "ro", "-s", str(b.get("speed") or 150),
                            "-p", "40", "-w", raw, "--", text], check=True, timeout=30)
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", raw,
                            "-ar", "8000", "-ac", "1", wav], check=True, timeout=30)
        if b.get("repeat"):
            loop = os.path.join(ROOT, "data", "tmp", f"say{cam_id}_loop.wav")
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                            "-stream_loop", str(int(b["repeat"]) - 1), "-i", wav, loop],
                           check=True, timeout=60)
            wav = loop
        secs = camctl.play_twoway(cam, camctl.to_mulaw(wav))
        return jsonify({"ok": True, "secunde": round(secs, 1)})
    except subprocess.CalledProcessError as e:
        return jsonify({"error": e.stderr.decode(errors="ignore")[-150:] if e.stderr else str(e)}), 500
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502


@bp.route("/api/cam/<int:cam_id>/volume", methods=["POST"])
def api_volume(cam_id):
    """Body: {speaker: 0..100}. Volumul difuzorului (two-way audio)."""
    cam = _cam_entry(cam_id)
    if not cam or not cam.get("features", {}).get("talk"):
        return jsonify({"error": t("camera nu are difuzor")}), 404
    b = request.get_json(silent=True) or {}
    try:
        camctl.set_speaker_volume(cam, int(b.get("speaker") or 50))
        return jsonify({"ok": True})
    except requests.RequestException as e:
        return jsonify({"error": str(e)}), 502

@bp.route("/api/cam/<int:cam_id>/detect", methods=["POST"])
def api_detect(cam_id):
    """Body: {on: bool} si/sau {auto_siren: bool} — detectia si sirena automata la caine."""
    if not any(c["id"] == cam_id for c in get_cameras()):
        return jsonify({"error": t("camera nu există")}), 404
    b = request.get_json(silent=True) or {}
    detect.set_detect(cam_id,
                      on=b.get("on") if "on" in b else None,
                      auto_siren=b.get("auto_siren") if "auto_siren" in b else None)
    return jsonify({"ok": True, **detect.cam_cfg(cam_id)})

# ---- descoperire camere in retea ----
@bp.route("/api/discover", methods=["POST"])
def api_discover():
    """Body: {cidr, user, pass}. Porneste scanarea (se interogheaza si credentialele
    camerelor deja adaugate, ca sa gaseasca si echipamente cu alte parole)."""
    b = request.get_json(silent=True) or {}
    cidr = (b.get("cidr") or "").strip()
    users = []
    if b.get("user"):
        users.append((b["user"].strip(), b.get("pass") or ""))
    for c in load_ext():  # credentialele deja stiute, ca fallback
        pair = (c["user"], c["pass"])
        if pair not in users:
            users.append(pair)
    if not users:
        return jsonify({"error": t("completează user și parola")}), 400
    known = {c["ip"] for c in load_ext()}
    if not discover.start(cidr, users, known):
        return jsonify({"error": t("o scanare rulează deja")}), 409
    return jsonify({"ok": True})


@bp.route("/api/discover")
def api_discover_status():
    return jsonify(discover.state)


@bp.route("/api/cameras/import", methods=["POST"])
def api_import():
    """Body: {cameras: [{ip,user,pass,name?}]}. Adauga in masa camerele bifate din scanare."""
    b = request.get_json(silent=True) or {}
    with EXT_LOCK:
        cams = load_ext()
        known = {c["ip"] for c in cams}
        added = []
        for item in b.get("cameras", []):
            ip = (item.get("ip") or "").strip()
            if not ip or ip in known:
                continue
            new_id = next_cam_id(cams)
            cam = {"id": new_id, "name": item.get("name") or f"Camera {new_id}",
                   "ip": ip, "port": int(item.get("port") or 554),
                   "user": item.get("user") or "admin", "pass": item.get("pass") or "",
                   "model": item.get("model"), "playback": False, "record": "continuous"}
            if item.get("vendor") == "onvif":
                cam["vendor"] = "onvif"
                cam["onvif"] = {"port": 80}
                try:
                    cam["onvif"].update(drivers.descopera_fluxuri(cam))
                except Exception as e:
                    logare.info(f"[server] import onvif {ip}: {e}")
                    continue
            try:
                cam["features"] = drivers.probe_features(cam)
                drivers.setup_camera(cam, cam["features"])
            except Exception as e:
                logare.info(f"[server] import {ip}: {e}")
                cam["features"] = {}
            cams.append(cam)
            known.add(ip)
            added.append({"id": new_id, "ip": ip, "name": cam["name"]})
        if added:
            audit.log("camere adăugate", ", ".join(a["name"] for a in added))
            save_ext(cams)
        sync_all_watchers(cams)
        reload_streams()
    return jsonify({"ok": True, "added": added})

# ---- configurare camera (nume, mod inregistrare, functii smart) ----
@bp.route("/api/cam/<int:cam_id>/config", methods=["POST"])
def api_cam_config(cam_id):
    """Body: {name?, record?: continuous|motion|off, smart?: {cheie: bool}}."""
    b = request.get_json(silent=True) or {}
    with EXT_LOCK:
        cams = load_ext()
        cam = next((c for c in cams if c["id"] == cam_id), None)
        if not cam:
            return jsonify({"error": t("camera nu există")}), 404
        reconnect = False
        if b.get("name"):
            cam["name"] = b["name"].strip()[:60]
        if b.get("record") in ("continuous", "motion", "off"):
            cam["record"] = b["record"]
        if isinstance(b.get("retention"), dict) or b.get("retention") is None and "retention" in b:
            r = b.get("retention")
            if r:
                cam["retention"] = {k: int(v) for k, v in r.items()
                                    if k in ("days", "event_days") and str(v).isdigit()}
            else:
                cam.pop("retention", None)
        if b.get("group") is not None:
            cam["group"] = (b["group"] or "Altele").strip()[:30]
        # datele de conexiune: se verifica INAINTE de salvare, ca sa nu ramai fara camera
        conn = {k: b[k] for k in ("ip", "port", "user", "pass") if b.get(k)}
        if conn:
            probe = {**cam, **conn}
            try:
                drivers.device_info(probe)
                if not drivers.este_hik(probe):
                    probe["onvif"] = {**(probe.get("onvif") or {}), **drivers.descopera_fluxuri(probe)}
                    cam["onvif"] = probe["onvif"]
            except Exception as e:
                msg = str(e)
                if "401" in msg or msg == t("autentificare esuata (user/parola)"):
                    return jsonify({"error": t("utilizator sau parola greșite")}), 401
                return jsonify({"error": t("nu pot contacta camera la {ip}: {e}", ip=probe["ip"], e=msg[:80])}), 502
            cam.update(conn)
            if "port" in conn:
                cam["port"] = int(conn["port"])
            reconnect = True
        errors = []
        for key, on in (b.get("smart") or {}).items():
            try:
                camctl.set_smart(cam, key, bool(on))
                cam.setdefault("features", {}).setdefault("smart", {})[key] = bool(on)
            except Exception as e:
                errors.append(f"{key}: {e}")
        save_ext(cams)
    audit.log("configurare camera", cam.get("name") or cam_id,
              {k: v for k, v in b.items() if k != "pass"})
    invalidate_cam_cache()
    if reconnect:      # adresa sau credentialele s-au schimbat -> refacem fluxurile
        sync_all_watchers(cams)
        reload_streams()
    if errors:
        return jsonify({"error": "; ".join(errors)}), 502
    return jsonify({"ok": True, "camera": {k: v for k, v in cam.items() if k != "pass"}})

@bp.route("/api/cameras/bulk", methods=["POST"])
def api_bulk():
    """Aplica aceleasi setari pe mai multe camere deodata.
    Body: {ids:[...], record?, group?, stream?{codec,kbps,fps,keyframe}, smart?{cheie:bool},
    detect?, auto_siren?}. Camerele se prelucreaza pe rand, cu pauza — la cereri rapide
    unele camere refuza conexiunea."""
    b = request.get_json(silent=True) or {}
    ids = [int(i) for i in (b.get("ids") or [])]
    if not ids:
        return jsonify({"error": t("nicio camera selectată")}), 400
    cams = load_ext()
    by_id = {c["id"]: c for c in cams}
    results, changed = [], False
    for cid in ids:
        cam = by_id.get(cid)
        if not cam:
            results.append({"id": cid, "ok": False, "err": t("inexistenta")})
            continue
        errs = []
        if b.get("record") in ("continuous", "motion", "off"):
            cam["record"] = b["record"]
            changed = True
        if b.get("group"):
            cam["group"] = b["group"].strip()[:30]
            changed = True
        st = b.get("stream") or {}
        if any(st.get(k) for k in ("codec", "kbps", "fps", "keyframe")):
            try:
                _set_stream_synced(cam, st.get("codec"), st.get("kbps"),
                                   st.get("fps"), st.get("keyframe"))
                _remember_desired(cid, st)
                record.reset_codec(cid)
            except Exception as e:
                errs.append(t("calitate: {e}", e=str(e)[:40]))
        if st.get("sub_keyframe"):
            try:
                _set_stream_synced(cam, keyframe=st["sub_keyframe"], ch=102)
            except Exception as e:
                errs.append(t("cadre-cheie: {e}", e=str(e)[:40]))
        for key, on in (b.get("smart") or {}).items():
            try:
                camctl.set_smart(cam, key, bool(on))
                cam.setdefault("features", {}).setdefault("smart", {})[key] = bool(on)
                changed = True
            except Exception as e:
                errs.append(f"{key}: {str(e)[:30]}")
        if b.get("detect") is not None:
            detect.set_detect(cid, on=bool(b["detect"]))
        if b.get("auto_siren") is not None:
            detect.set_detect(cid, auto_siren=bool(b["auto_siren"]))
        results.append({"id": cid, "ok": not errs, "err": "; ".join(errs) or None})
        time.sleep(0.4)   # ritm lent: camerele refuza conexiunile prea dese
    if changed:
        save_ext(cams)
        invalidate_cam_cache()
    audit.log("operatie în masa", f"{len(ids)} camere",
              {k: v for k, v in b.items() if k != "ids"})
    return jsonify({"ok": True, "results": results,
                    "reusite": sum(1 for r in results if r["ok"]), "total": len(results)})


@bp.route("/api/cam/<int:cam_id>/stream", methods=["GET", "POST"])
def api_cam_stream(cam_id):
    """GET = setarile de calitate ale mainstream-ului; POST {codec,kbps,fps} le schimba.
    Afecteaza direct spatiul ocupat: inregistram exact ce trimite camera (fara recodare)."""
    cam = _cam_entry(cam_id)
    if not cam:
        return jsonify({"error": t("camera nu există")}), 404
    if not drivers.este_hik(cam):
        return jsonify({"error": t("calitatea fluxului se seteaza din interfața camerei (ONVIF)"),
                        "vendor": "onvif"}), 400
    try:
        if request.method == "POST":
            b = request.get_json(silent=True) or {}
            camctl.set_stream(cam, 101, codec=b.get("codec"), kbps=b.get("kbps"),
                              fps=b.get("fps"), keyframe=b.get("keyframe"))
            # NVR-ul administreaza camerele proxied si le RE-IMPINGE configuratia lui
            # periodic; daca nu o actualizam si acolo, setarile noastre sunt anulate.
            if cam.get("nvr_channel") and not settings.nvr_retras():
                try:
                    nvr_cam = {"ip": C.NVR_IP, "user": C.NVR_USER, "pass": C.NVR_PASS}
                    camctl.set_stream(nvr_cam, cam["nvr_channel"] * 100 + 1,
                                      codec=b.get("codec"), kbps=b.get("kbps"),
                                      fps=b.get("fps"))
                except Exception as e:
                    logare.info(f"[server] sync NVR cam{cam_id}: {e}")
            record.reset_codec(cam_id)
        return jsonify(camctl.get_stream(cam, 101))
    except (requests.RequestException, RuntimeError, ET.ParseError) as e:
        return jsonify({"error": str(e)}), 502

@bp.route("/api/cam/<int:cam_id>/health", methods=["POST"])
def api_cam_health(cam_id):
    """Verifica efectiv fluxul video al camerei (nu doar ca raspunde la interogari)."""
    cam = _cam_entry(cam_id)
    if not cam:
        return jsonify({"error": t("camera nu există")}), 404
    return jsonify({"sub": health.check_stream(cam, 102),
                    "main": health.check_stream(cam, 101)})

@bp.route("/api/cam/<int:cam_id>/reprobe", methods=["POST"])
def api_reprobe(cam_id):
    """Reinterogheaza ce stie camera sa faca — util dupa un upgrade de firmware sau
    dupa ce ai conectat un difuzor/senzor."""
    with EXT_LOCK:
        cams = load_ext()
        cam = next((c for c in cams if c["id"] == cam_id), None)
        if not cam:
            return jsonify({"error": t("camera nu există")}), 404
        try:
            # Ce s-a aflat prin proba (camera chiar se roteste? chiar pastreaza pozitiile?) nu
            # poate fi dedus din interogarea obisnuita — firmware-ul raporteaza PTZ si acolo
            # unde nu exista. Daca l-am sterge la fiecare resondare, meniul ar disparea de pe
            # camera care se roteste si ar trebui reluata proba de fiecare data.
            pastrate = {k: v for k, v in (cam.get("features") or {}).items()
                        if k in ("ptz", "presets")}
            cam["features"] = drivers.probe_features(cam)
            cam["features"].update(pastrate)
            drivers.setup_camera(cam, cam["features"])
            if not drivers.este_hik(cam):
                cam["onvif"] = {**(cam.get("onvif") or {}), **drivers.descopera_fluxuri(cam)}
        except Exception as e:
            return jsonify({"error": str(e)[:120]}), 502
        save_ext(cams)
    invalidate_cam_cache()
    reload_streams()
    return jsonify({"ok": True, "features": cam["features"]})


@bp.route("/api/cam/<int:cam_id>/reboot", methods=["POST"])
def api_reboot(cam_id):
    """Reporneste camera (revine in ~40 de secunde)."""
    cam = _cam_entry(cam_id)
    if not cam:
        return jsonify({"error": t("camera nu există")}), 404
    try:
        s = requests.Session()
        s.auth = HTTPDigestAuth(cam["user"], cam["pass"])
        r = s.put(f"http://{cam['ip']}/ISAPI/System/reboot", timeout=15)
        r.raise_for_status()
        return jsonify({"ok": True})
    except requests.RequestException as e:
        return jsonify({"error": str(e)[:100]}), 502
