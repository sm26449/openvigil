#!/usr/bin/env python3
"""Blueprint: analiza AI — alarme/recatalogare, reguli, cautare semantica, urmarire, LPR.

Blocul din mijloc al lui server.py (audit #8), ultimul extras: snapshot-urile de alarma
(lista, recatalogare, stergere), motorul de reguli, intrebari libere + cautare in index
(ask/search/semantic), detectia pe regiuni (tile), urmarirea obiectelor (track) si
numerele de inmatriculare (plates).
"""
import os
import re
import time

from flask import Blueprint, jsonify, request, send_file

import ai_client
import audit
import faces
import plates
import rules
import semantic
import settings
import snaps
import track
from i18n import t
from server import ROOT, _visible_ids, get_cameras, load_ext

bp = Blueprint("analytics", __name__)


# ---- recatalogare detectii (corectare eticheta / stergere din lightbox) ----
def _snap_path(url):
    """Valideaza un URL /snapshots/<zi>/<fisier> si intoarce calea pe disc (sau None)."""
    m = re.match(r"^/snapshots/(\d{4}-\d{2}-\d{2})/([\w.-]+\.jpg)$", url or "")
    if not m:
        return None
    p = os.path.join(ROOT, "data", "snapshots", m[1], m[2])
    return p if os.path.isfile(p) else None


@bp.route("/api/alarms/relabel", methods=["POST"])
def api_relabel():
    """Body: {url, label}. Redenumeste snapshot-ul cu eticheta corecta (recatalogare)."""
    b = request.get_json(silent=True) or {}
    p = _snap_path(b.get("url"))
    label = (b.get("label") or "").strip()
    if not p or not re.match(r"^[A-Za-z]{2,20}$", label):
        return jsonify({"error": t("cerere invalida")}), 400
    d, fn = os.path.split(p)
    m = re.match(r"^(\d{6}_cam\d+_)(.+)\.jpg$", fn)
    if not m:
        return jsonify({"error": t("nume de fișier necunoscut")}), 400
    new = os.path.join(d, f"{m[1]}{label}.jpg")
    os.rename(p, new)
    return jsonify({"ok": True, "url": f"/snapshots/{os.path.basename(d)}/{os.path.basename(new)}"})


@bp.route("/api/alarms/delete", methods=["POST"])
def api_alarm_delete():
    """Body: {url}. Sterge un snapshot gresit (detectie falsa)."""
    p = _snap_path((request.get_json(silent=True) or {}).get("url"))
    if not p:
        return jsonify({"error": t("cerere invalida")}), 400
    os.remove(p)
    return jsonify({"ok": True})


# ---- motorul de reguli ----
@bp.route("/api/rules")
def api_rules():
    """Regulile + dictionarele pentru formular (tipuri de declansatoare si actiuni,
    plus ce stie fiecare camera sa faca — formularul arata doar actiunile posibile)."""
    caps = {c["id"]: {"siren": bool((c.get("features") or {}).get("siren")),
                      "strobe": bool((c.get("features") or {}).get("strobe")),
                      "talk": bool((c.get("features") or {}).get("talk")),
                      "io_out": bool((c.get("features") or {}).get("io_out"))}
            for c in load_ext()}
    return jsonify({"rules": sorted(rules.load(), key=lambda r: r.get("nume", "")),
                    "triggers": {k: t(v) for k, v in rules.TRIGGER_TYPES.items()},
                    "actions": {k: t(v) for k, v in rules.ACTION_TYPES.items()},
                    "capabilities": caps, "zile": [t(z) for z in rules.ZILE],
                    "groups": sorted({c.get("group") or "Altele" for c in load_ext()}),
                    "persons": [p["name"] for p in faces.persons()],
                    "plates": sorted(plates.known())})


@bp.route("/api/rules", methods=["POST"])
def api_rules_save():
    try:
        r = rules.upsert(request.get_json(silent=True) or {})
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    audit.log("regula salvată", r["nume"], {"declansator": r["declansator"]})
    return jsonify({"ok": True, "rule": r})


@bp.route("/api/rules/<rule_id>", methods=["DELETE"])
def api_rules_delete(rule_id):
    try:
        rules.delete(rule_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    audit.log("regula ștearsă", rule_id)
    return jsonify({"ok": True})


@bp.route("/api/rules/<rule_id>/test", methods=["POST"])
def api_rules_test(rule_id):
    """Declanseaza regula ACUM, ocolind conditiile — ca sa vezi ca actiunile merg."""
    r = next((x for x in rules.load() if x["id"] == rule_id), None)
    if not r:
        return jsonify({"error": t("regula nu există")}), 404
    cam_id = (r.get("camere") or [None])[0]
    if cam_id is None:
        cam_id = next((c["id"] for c in load_ext()), None)
    rules._run_actions(r, r["declansator"]["tip"], cam_id, {"proba": True})
    return jsonify({"ok": True, "nota": t("acțiunile au rulat; vezi jurnalul de evenimente")})


@bp.route("/api/faces/history")
def api_faces_history():
    """?name=&limit= — aparitiile recunoscute ("cand a mai venit X pe la noi?"), doar pe
    camerele vizibile contului."""
    vis = _visible_ids()
    h = faces.history(nume=request.args.get("name") or None,
                      limit=min(500, max(1, request.args.get("limit", 100, type=int))))
    return jsonify({"history": [x for x in h if vis is None or x.get("cam") in vis]})


# ---- cautare semantica in alarme ----
@bp.route("/api/ask")
def api_ask():
    """?q=cand a venit masina de gunoi — intrebare libera, raspuns in romana
    + aparitiile-dovada. Merge si pentru vieweri/operatori (pe camerele lor)."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": t("scrie intrebarea")}), 400
    if not semantic.enabled():
        return jsonify({"error": t("căutarea semantică nu e pornită (Setări → Detecție)")}), 409
    nume = {c["id"]: c["name"] for c in get_cameras()}
    vis = _visible_ids()
    cam = request.args.get("cam", type=int)
    # indexul + modelele stau in nvr-ai (#9 pas 3); la esec, local (SigLIP lazy in web)
    if ai_client.remote():
        d = ai_client.ask(q, vis, nume, cam)
        if d is not None:
            return jsonify(d)
    import ask
    try:
        return jsonify(ask.answer(q, visible=vis, cam_names=nume, cam=cam))
    except Exception as e:
        return jsonify({"error": t("întrebare eșuată: {e}", e=str(e)[:100])}), 500


@bp.route("/api/search")
def api_search():
    """?q=descriere libera&days=14&limit=40 — cauta in snapshot-urile indexate.
    Merge si pentru vieweri (rezultatele se filtreaza pe camerele lor)."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": t("scrie ce cauti")}), 400
    if not semantic.enabled():
        return jsonify({"error": t("căutarea semantică nu e pornită (Setări → Detecție)")}), 409
    days = min(90, max(1, request.args.get("days", 14, type=int)))
    limit = min(100, max(1, request.args.get("limit", 40, type=int)))
    vis = _visible_ids()
    rez = ai_client.semantic_search(q, days, limit, vis) if ai_client.remote() else None
    if rez is None:
        try:
            rez = semantic.search(q, days=days, limit=limit, visible=vis)
        except Exception as e:
            return jsonify({"error": t("căutare eșuată: {e}", e=str(e)[:100])}), 500
    return jsonify({"q": q, "results": rez})


@bp.route("/api/semantic/status")
def api_semantic_status():
    if ai_client.remote():
        d = ai_client.semantic_status()
        if d is not None:
            return jsonify(d)
    return jsonify(semantic.status())


@bp.route("/api/tile", methods=["GET", "POST"])
def api_tile():
    """Camerele cu detectie pe regiuni (obiecte mici la distanta). GET = lista,
    POST {cams:[...]} le seteaza."""
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        cunoscute = {c["id"] for c in get_cameras()}
        ids = sorted({int(c) for c in (b.get("cams") or [])} & cunoscute)
        settings.update({"tile_cams": ids})
        audit.log("detecție pe regiuni", detail={"camere": ids})
        return jsonify({"ok": True, "cams": ids})
    return jsonify({"cams": sorted(settings.get("tile_cams") or [])})


@bp.route("/api/semantic/config", methods=["POST"])
def api_semantic_config():
    b = request.get_json(silent=True) or {}
    if "enabled" in b:
        settings.update({"semantic_enabled": bool(b["enabled"])})
        if b["enabled"]:
            if ai_client.remote():
                ai_client.warmup(semantic=True)
                ai_client.semantic_backfill()   # istoricul intra in index de la sine
            else:
                semantic.warmup_async()
                semantic.start_backfill()
    audit.log("setări căutare semantică", detail={"enabled": b.get("enabled")})
    return jsonify({"ok": True})


@bp.route("/api/semantic/backfill", methods=["POST"])
def api_semantic_backfill():
    if ai_client.remote():
        d = ai_client.semantic_backfill()
        if d is not None:
            return jsonify(d)
    started = semantic.start_backfill()
    return jsonify({"ok": True, "started": started, **semantic.backfill_state})


# ---- urmarirea obiectelor (numarare exacta) ----
@bp.route("/api/track")
def api_track():
    """Config + starea lucratorilor de urmarire (camere, contoare azi).
    Cand ai_service e pornit, starea vine de la nvr-ai (proxy)."""
    if settings.get("ai_service"):
        return jsonify({"cams": sorted(track.enabled_cams()),
                        "state": ai_client.track_state() or {}})
    return jsonify({"cams": sorted(track.enabled_cams()), "state": track.snapshot()})


@bp.route("/api/track/config", methods=["POST"])
def api_track_config():
    """Body: {cams: [id, ...]} — pe ce camere ruleaza urmarirea."""
    b = request.get_json(silent=True) or {}
    ids = sorted({int(c) for c in (b.get("cams") or [])})
    cunoscute = {c["id"] for c in get_cameras()}
    ids = [c for c in ids if c in cunoscute]
    settings.update({"track_cams": ids})
    track.sync()
    audit.log("urmărire obiecte", detail={"camere": ids})
    return jsonify({"ok": True, "cams": ids})


# ---- numere de inmatriculare (LPR) ----
@bp.route("/api/plates")
def api_plates():
    """Starea LPR: setari, masinile cunoscute, ultimele citiri (cu poze).
    ?q=B123 cauta in istoricul citirilor dupa numar (partial) sau nume."""
    s = settings.load()
    q = (request.args.get("q") or "").strip().upper()
    reads = plates.reads(limit=500 if q else 100)
    vis = _visible_ids()
    if vis is not None:
        reads = [r for r in reads if r.get("cam") in vis]
    if q:
        reads = [r for r in reads
                 if q in r.get("numar", "") or q in (r.get("nume") or "").upper()][:100]
    return jsonify({
        "enabled": s["lpr_enabled"], "min_conf": s["lpr_min_conf"],
        "sure_conf": s.get("lpr_sure_conf", 0.9),
        "notify_known": s["lpr_notify_known"], "notify_all": s["lpr_notify_all"],
        "notify_unknown": bool(s.get("lpr_notify_unknown")),
        "alert_cams": [int(c) for c in (s.get("lpr_alert_cams") or [])],
        "stats": plates.stats(),
        "known": [{"plate": p, "name": n} for p, n in sorted(plates.known().items())],
        "reads": [{**r, "url": f"/api/plates/img/{r['crop']}" if r.get("crop") else None}
                  for r in reads],
    })


@bp.route("/api/plates/config", methods=["POST"])
def api_plates_config():
    b = request.get_json(silent=True) or {}
    upd = {}
    if "enabled" in b:
        upd["lpr_enabled"] = bool(b["enabled"])
    if "min_conf" in b:
        upd["lpr_min_conf"] = max(0.5, min(0.99, float(b["min_conf"])))
    if "notify_known" in b:
        upd["lpr_notify_known"] = bool(b["notify_known"])
    if "notify_all" in b:
        upd["lpr_notify_all"] = bool(b["notify_all"])
    if "sure_conf" in b:
        upd["lpr_sure_conf"] = max(0.5, min(0.99, float(b["sure_conf"])))
    if "notify_unknown" in b:
        upd["lpr_notify_unknown"] = bool(b["notify_unknown"])
    if "alert_cams" in b:
        try:
            upd["lpr_alert_cams"] = sorted({int(c) for c in (b["alert_cams"] or [])})
        except (TypeError, ValueError):
            return jsonify({"error": t("alert_cams: listă de id-uri de camere")}), 400
    settings.update(upd)
    if upd.get("lpr_enabled"):
        # modelul LPR sta in nvr-ai cand serviciul e viu, altfel local
        if ai_client.remote():
            ai_client.warmup(lpr=True)
        else:
            plates.warmup_async()
    audit.log("setări lpr", detail=upd)
    return jsonify({"ok": True})


@bp.route("/api/plates/known", methods=["POST"])
def api_plates_known():
    """Body: {plate, name} — da un nume unei masini (sau il schimba)."""
    b = request.get_json(silent=True) or {}
    try:
        plate = plates.set_known(b.get("plate"), b.get("name"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("mașină cunoscută salvată", plate,
              {"nume": b.get("name")})
    return jsonify({"ok": True, "plate": plate})


@bp.route("/api/plates/known/<plate>", methods=["DELETE"])
def api_plates_known_del(plate):
    try:
        plates.del_known(plate)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("mașină cunoscută ștearsă", plate)
    return jsonify({"ok": True})


@bp.route("/api/plates/clear", methods=["POST"])
def api_plates_clear():
    plates.clear_log()
    audit.log("jurnal lpr golit")
    return jsonify({"ok": True})


@bp.route("/api/plates/img/<path:rel>")
def api_plates_img(rel):
    p = plates.safe_path(rel)
    if not p or not os.path.isfile(p):
        return jsonify({"error": t("nu există")}), 404
    return send_file(p, mimetype="image/jpeg", max_age=60)


_FLUX_VEH = {"car", "truck", "bus", "motorcycle", "vehicledetection", "parkingdetection"}
_FLUX_ANIM = {"dog", "cat", "bird", "chicken", "pigeon", "horse", "sheep", "cow", "bear"}
_FLUX_OM = {"person", "peopleDetection", "running", "loitering"}
_FLUX_PLACUTA = re.compile(r"^[A-Z]{1,2}\d{2,3}[A-Z]{3}$")


def _flux_categorie(label, persoane):
    """Chip-ul din flux: om / vehicul / animal / fata / numar / miscare / alarma.
    Miscarea simpla (VMD) e zgomotul de fond — are chip propriu si nu intra in „Toate"."""
    if label in ("VMD", "motion"):
        return "miscare"
    if label in ("shelteralarm", "tamperdetection", "scenechangedetection", "defocusdetection"):
        return "sabotaj"
    if label in _FLUX_OM:
        return "om"
    if label in _FLUX_VEH:
        return "vehicul"
    if label in _FLUX_ANIM:
        return "animal"
    if _FLUX_PLACUTA.match(label):
        return "numar"
    if label in persoane or label == "faceDetection":
        return "fata"
    return "alarma"


_flux_cache = {"zi": None, "mtime": None, "items": []}


def _flux_lista(day):
    """Snapshot-urile zilei, cu cache pe mtime-ul directorului: fiecare tab Live cere la 10 s,
    iar listarea a 5.000 de fisiere + regex de fiecare data e inutila cand nu s-a adaugat nimic."""
    d = os.path.join(snaps.SNAP_DIR, day)
    try:
        mt = os.path.getmtime(d)
    except OSError:
        return []
    if _flux_cache["zi"] == day and _flux_cache["mtime"] == mt:
        return _flux_cache["items"]
    items = snaps.list_day(day)
    _flux_cache.update(zi=day, mtime=mt, items=items)
    return items


@bp.route("/api/flux")
def api_flux():
    """Fluxul de evenimente de langa Live: ultimele cadre salvate (detectii, alarme, fete,
    numere), cele mai noi primele, cu categoria pentru filtre. ?limit= (implicit 40),
    ?dupa=HH:MM:SS -> doar cele mai noi decat momentul (pentru reimprospatare ieftina)."""
    limit = max(1, min(200, request.args.get("limit", 40, type=int)))
    dupa = request.args.get("dupa") or ""
    day = time.strftime("%Y-%m-%d")
    vis = _visible_ids()
    nume = {c["id"]: c.get("name") or f"cam {c['id']}" for c in load_ext()}
    persoane = {p["name"] for p in faces.persons()}
    # miscarea simpla (VMD) e de 10x mai multa decat restul: o numaram separat, ca sa nu
    # impinga afara evenimentele reale mai vechi din fereastra — pana la `limit` din fiecare
    out, n_ev, n_mi = [], 0, 0
    for it in _flux_lista(day):
        if vis is not None and it["cam"] not in vis:
            continue
        if dupa and it["time"] <= dupa:
            break
        cat = _flux_categorie(it["label"], persoane)
        if cat == "miscare":
            if n_mi >= limit:
                continue
            n_mi += 1
        else:
            if n_ev >= limit:
                continue
            n_ev += 1
        out.append({**it, "zi": day, "cam_nume": nume.get(it["cam"], f"cam {it['cam']}"), "cat": cat,
                    "thumb": "/api/flux/thumb/" + it["url"].split("/snapshots/", 1)[1]})
        if n_ev >= limit and n_mi >= limit:
            break
    return jsonify({"zi": day, "cadre": out})


@bp.route("/api/sabotaj")
def api_sabotaj():
    """Cate alarme de sabotaj (obiectiv acoperit, sabotaj, scena schimbata) a dat fiecare
    camera azi (?zi=): {cam: n}. Multe pe o camera, noaptea, inseamna IR/insecte, nu un hot."""
    zi = request.args.get("zi") or time.strftime("%Y-%m-%d")
    vis = _visible_ids()
    out = {}
    for it in snaps.list_day(zi):
        if it["label"] in ("shelteralarm", "tamperdetection", "scenechangedetection", "defocusdetection") \
                and (vis is None or it["cam"] in vis):
            out[it["cam"]] = out.get(it["cam"], 0) + 1
    return jsonify({"zi": zi, "sabotaj": out})


@bp.route("/api/alarms")
def api_alarms():
    """?date=YYYY-MM-DD (implicit azi). Cu grup=1 intoarce intamplari, nu cadre separate."""
    day = request.args.get("date") or time.strftime("%Y-%m-%d")
    vis = _visible_ids()
    if request.args.get("grup"):
        cam = request.args.get("cam", type=int)
        if vis is not None and cam is not None and cam not in vis:
            return jsonify({"incidente": [], "cadre": 0})
        inc = snaps.incidents(day, cam)
        if vis is not None:
            inc = [i for i in inc if i["cam"] in vis]
        return jsonify({"incidente": inc, "cadre": sum(i["nr"] for i in inc)})
    items = snaps.list_day(day, limit=2000)
    if vis is not None:
        items = [i for i in items if i["cam"] in vis]
    return jsonify(items)
