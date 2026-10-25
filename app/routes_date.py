#!/usr/bin/env python3
"""Blueprint: date care supravietuiesc — lantul de custodie al segmentelor si copia
nocturna criptata.

/api/segmente: amprentele SHA-256 ale segmentelor unei camere intr-o zi (orice rol, cu
restrictia pe camere); /api/segmente/verifica recalculeaza hash-ul unui segment.
/api/backup/*: doar admin (guard-ul lasa /api/backup/stare la GET pentru admin oricum).
"""
import os
import time

from flask import Blueprint, jsonify, request

import audit
import backup_nocturn
import jobs
import lant
from server import current_user

bp = Blueprint("date", __name__)


def _zi(s):
    try:
        return time.mktime(time.strptime((s or "").strip(), "%Y-%m-%d"))
    except ValueError:
        return None


@bp.route("/api/segmente")
def api_segmente():
    """?channel=&day= -> segmentele zilei cu hash-ul de la inchidere (lant de custodie)."""
    try:
        cam = int(request.args["channel"])
    except (KeyError, ValueError):
        return jsonify({"error": "channel lipsa"}), 400
    t0 = _zi(request.args.get("day") or time.strftime("%Y-%m-%d"))
    if t0 is None:
        return jsonify({"error": "zi invalida"}), 400
    seg = lant.lista(cam, since=t0, until=t0 + 86400)
    for s in seg:
        s["exista"] = os.path.exists(s["cale"])
        s.pop("cale", None)                 # calea de pe disc nu e treaba browserului
    return jsonify({"camera": cam, "segmente": seg})


@bp.route("/api/segmente/verifica")
def api_segmente_verifica():
    """?channel=&nume= -> recalculeaza hash-ul si il compara cu amprenta de la inchidere."""
    try:
        cam = int(request.args["channel"])
        nume = os.path.basename(request.args["nume"])
    except (KeyError, ValueError):
        return jsonify({"error": "parametri lipsa"}), 400
    r = lant.verifica(cam, nume)
    audit.log("verificare amprenta segment", f"cam{cam} {nume}", {"ok": r.get("ok")})
    return jsonify({"camera": cam, "nume": nume, **r})


@bp.route("/api/backup/stare")
def api_backup_stare():
    import settings
    s = settings.load()
    return jsonify({"stare": backup_nocturn.stare(), "copii": backup_nocturn.lista()[:20],
                    "director": backup_nocturn.backup_dir(),
                    "cheie_amprenta": backup_nocturn.amprenta_cheie(),
                    "setari": {k: s.get(k) for k in ("backup_ora", "backup_dir", "backup_remote",
                                                     "backup_pastreaza", "backup_fete")}})


@bp.route("/api/backup/acum", methods=["POST"])
def api_backup_acum():
    """Copie la cerere, ca job (dureaza secunde-minute, trimiterea externa poate dura)."""
    u = current_user() or {}
    return jsonify(jobs.submit("backup", {"cine": u.get("user")}, who=u.get("user"))), 202


def _job_backup(job):
    try:
        return backup_nocturn.ruleaza(progres=job.progres, cine=job.parametri.get("cine") or "admin")
    except Exception as e:
        raise jobs.JobError(f"copia a eșuat: {str(e)[:150]}")


@bp.route("/api/backup/cheie")
def api_backup_cheie():
    """Cheia de criptare — fara ea copiile sunt inutile; se arata o data, de notat separat."""
    audit.log("cheia de backup afisata")
    return jsonify({"cheie": backup_nocturn.cheie(), "amprenta": backup_nocturn.amprenta_cheie(),
                    "nota": "Pastreaz-o in alta parte decat serverul (manager de parole). "
                            "Refacere: scripts/restaureaza-backup.py <fisier.enc> <destinatie>"})


jobs.register("backup", _job_backup)
