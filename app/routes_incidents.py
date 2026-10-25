#!/usr/bin/env python3
"""Blueprint: incidente + dosare de caz + rezumat zilnic.

Primul grup extras din server.py (audit #8 — spargere in blueprints). Helper-urile
partajate (auth, camere vizibile, randare clip) vin din `server`, care e importat ca
MODUL (prin server_main.py), nu rulat ca __main__ — altfel `from server import` ar
reimporta dublu. Guard-ul de autentificare e `@app.before_request` global, deci se
aplica automat si rutelor de aici.
"""
import hashlib
import json
import os
import re
import shutil
import time
import zipfile

from flask import Blueprint, jsonify, request

import audit
import cases as cases_mod
import jobs
import digest as digest_mod
import incidents as incidents_mod
from server import ROOT, _visible_ids, current_user, get_cameras, render_clip

bp = Blueprint("incidents", __name__)


@bp.route("/api/incidents")
def api_incidents():
    """Coada de incidente (sev>=atentie), prioritizate, cu starea de tratare.
    ?noi=1 doar cele netratate. Filtrata pe camerele vizibile (operator/viewer)."""
    vis = _visible_ids()
    return jsonify({
        "incidente": incidents_mod.feed(
            limit=min(120, max(1, request.args.get("limit", 60, type=int))),
            doar_noi=bool(request.args.get("noi")), visible=vis),
        "counts": incidents_mod.counts(visible=vis)})


@bp.route("/api/incidents/<eid>/stare", methods=["POST"])
def api_incident_state(eid):
    """Body: {stare: confirmat|fals|nou}. Operatorul preia/inchide un incident."""
    stare = (request.get_json(silent=True) or {}).get("stare")
    try:
        st = incidents_mod.set_state(eid, stare, who=(current_user() or {}).get("user"))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("incident " + str(stare), eid)
    return jsonify({"ok": True, **st})


@bp.route("/api/digest/text")
def api_digest_text():
    """Textul rezumatului zilei (?day=), asa cum ar pleca pe Telegram — fara sa-l trimita."""
    return jsonify({"text": digest_mod.text(request.args.get("day") or None)})


@bp.route("/api/digest/trimite", methods=["POST"])
def api_digest_trimite():
    """Trimite rezumatul zilei acum pe Telegram/email (test sau la cerere). Body: {day?}."""
    b = request.get_json(silent=True) or {}
    try:
        msg = digest_mod.trimite(b.get("day") or None, sync=True)
    except Exception as e:
        return jsonify({"error": f"nu s-a putut trimite: {str(e)[:120]}"}), 400
    audit.log("rezumatul zilei trimis", b.get("day") or "azi")
    return jsonify({"ok": True, "text": msg})


@bp.route("/api/digest")
def api_digest():
    """Rezumatul zilei (?day=YYYY-MM-DD, implicit azi): incidente, camere, ore de varf.
    Filtrat pe camerele vizibile pentru operator/viewer."""
    return jsonify(digest_mod.build(day=request.args.get("day") or None,
                                    visible=_visible_ids()))


def _case_visible(case, vis):
    """Un operator restrans vede un caz doar daca TOATE probele lui sunt de pe camere
    permise — altfel dosarul (cu clipuri de pe camere neautorizate) s-ar scurge."""
    if vis is None:
        return True
    return all(int(p.get("cam", -1)) in vis for p in case.get("probe", []))


@bp.route("/api/cases", methods=["GET", "POST"])
def api_cases():
    """GET: lista cazurilor. POST {titlu, descriere}: deschide un caz nou."""
    if request.method == "POST":
        b = request.get_json(silent=True) or {}
        try:
            c = cases_mod.create(b.get("titlu"), who=(current_user() or {}).get("user"),
                                 descriere=b.get("descriere", ""))
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        audit.log("caz nou", c["titlu"])
        return jsonify(c)
    vis = _visible_ids()
    return jsonify({"cazuri": [c for c in cases_mod.lista() if _case_visible(c, vis)]})


@bp.route("/api/cases/<cid>", methods=["GET", "DELETE"])
def api_case(cid):
    """GET: cazul cu toate probele. DELETE: sterge cazul."""
    if request.method == "DELETE":
        c = cases_mod.get(cid)
        if c and not _case_visible(c, _visible_ids()):
            return jsonify({"error": "nu ai acces la acest caz"}), 403
        if not cases_mod.delete(cid):
            return jsonify({"error": "caz inexistent"}), 404
        audit.log("caz sters", cid)
        return jsonify({"ok": True})
    c = cases_mod.get(cid)
    if not c:
        return jsonify({"error": "caz inexistent"}), 404
    if not _case_visible(c, _visible_ids()):
        return jsonify({"error": "nu ai acces la acest caz"}), 403
    return jsonify(c)


@bp.route("/api/cases/<cid>/proba", methods=["POST"])
def api_case_add(cid):
    """Body {cam, start, end, nota, event_id}: adauga o proba (interval) la caz."""
    b = request.get_json(silent=True) or {}
    vis = _visible_ids()
    if vis is not None:      # nu poti adauga proba de pe o camera pe care n-o vezi
        try:
            if int(b.get("cam", -1)) not in vis:
                return jsonify({"error": "nu ai acces la această camera"}), 403
        except (ValueError, TypeError):
            return jsonify({"error": "cam invalid"}), 400
        c0 = cases_mod.get(cid)   # nici la un caz cu probe de pe camere straine
        if c0 and not _case_visible(c0, vis):
            return jsonify({"error": "nu ai acces la acest caz"}), 403
    try:
        c = cases_mod.add_proba(cid, b["cam"], b["start"], b["end"],
                                nota=b.get("nota", ""), event_id=b.get("event_id"),
                                who=(current_user() or {}).get("user"))
    except KeyError:
        return jsonify({"error": "caz inexistent"}), 404
    except (ValueError, TypeError):
        return jsonify({"error": "date proba invalide (cam/start/end)"}), 400
    return jsonify(c)


@bp.route("/api/cases/<cid>/proba/<pid>", methods=["DELETE"])
def api_case_del(cid, pid):
    try:
        ok = cases_mod.del_proba(cid, pid)
    except KeyError:
        return jsonify({"error": "caz inexistent"}), 404
    return jsonify({"ok": ok})


@bp.route("/api/cases/<cid>/export")
def api_case_export(cid):
    """Dosarul complet ca ZIP: fiecare proba = clip cu watermark + manifest.json cu
    descrierea si suma SHA-256 a fiecarui fisier (dosar verificabil, gata de predat).
    Dureaza (un ffmpeg per proba), deci 202 + job; ZIP-ul vine din /api/jobs/<id>/fisier."""
    c = cases_mod.get(cid)
    if not c:
        return jsonify({"error": "caz inexistent"}), 404
    if not c.get("probe"):
        return jsonify({"error": "cazul nu are nicio proba"}), 400
    vis = _visible_ids()
    if not _case_visible(c, vis):
        return jsonify({"error": "nu ai acces la acest caz"}), 403
    return jsonify(jobs.submit("dosar", {"cid": cid, "vis": sorted(vis) if vis is not None else None},
                               who=(current_user() or {}).get("user"))), 202


def _job_dosar(job):
    cid, vis = job.parametri["cid"], job.parametri.get("vis")
    vis = set(vis) if vis is not None else None
    c = cases_mod.get(cid)
    if not c or not c.get("probe"):
        raise jobs.JobError("cazul nu (mai) există sau nu are probe")
    tmpdir = os.path.join(ROOT, "data", "tmp", f"caz_{cid}_{job.id}")
    os.makedirs(tmpdir, exist_ok=True)
    manifest = {"caz": {k: c[k] for k in ("id", "titlu", "descriere", "creat", "creat_de")},
                "exportat": time.strftime("%Y-%m-%d %H:%M:%S"),
                "exportat_de": job.creat_de, "probe": []}
    fisiere = []
    n = len(c["probe"])
    try:
        for i, p in enumerate(c["probe"], 1):
            if job.anulat:
                raise jobs.JobError("anulat")
            job.progres(int((i - 1) / n * 90), f"proba {i} din {n}")
            ch = int(p["cam"])
            if vis is not None and ch not in vis:
                continue   # operator restrans: sare probele din camere fara acces
            cam = next((x for x in get_cameras() if x["id"] == ch), {})
            nume = re.sub(r"[^\w-]", "_", cam.get("name") or f"cam{ch}")[:30]
            fn = f"{i:02d}_cam{ch}_{nume}.mp4"
            out = os.path.join(tmpdir, fn)
            ok, err = render_clip(ch, p["start"], p["end"], out, wm=True)
            if not ok:
                manifest["probe"].append({**p, "fisier": None, "eroare": err})
                continue
            with open(out, "rb") as fh:
                sha = hashlib.sha256(fh.read()).hexdigest()
            manifest["probe"].append({
                "fisier": fn, "cam": ch, "camera": cam.get("name"),
                "start": p["start"], "end": p["end"], "nota": p.get("nota"),
                "sha256": sha, "octeti": os.path.getsize(out)})
            fisiere.append((out, fn))
        job.progres(92, "impachetez")
        man_path = os.path.join(tmpdir, "manifest.json")
        with open(man_path, "w") as fh:
            json.dump(manifest, fh, ensure_ascii=False, indent=1)
        zpath = job.cale("zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:
            z.write(man_path, "manifest.json")
            for out, fn in fisiere:
                z.write(out, fn)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    audit.log("caz exportat", c["titlu"], who=job.creat_de)
    slug = re.sub(r"[^\w-]", "_", c["titlu"])[:40]
    job.fisier(zpath, f"caz_{slug}.zip", "application/zip")
    return {"probe": len(fisiere), "octeti": os.path.getsize(zpath)}


jobs.register("dosar", _job_dosar)
