#!/usr/bin/env python3
"""Blueprint: coada de joburi — stare, lista, descarcarea rezultatului, anulare.

Rutele care creeaza joburi stau la locul lor (export/clip in routes_media, camera noua in
routes_cameras, dosar in routes_incidents) si raspund 202 + jobul. Aici e doar urmarirea.
Un utilizator vede si opreste doar joburile lui; adminul le vede pe toate.
"""
from flask import Blueprint, jsonify, request, send_file

import jobs
from server import current_user

bp = Blueprint("jobs", __name__)


def _al_meu(j):
    u = current_user() or {}
    return u.get("role") == "admin" or j.get("creat_de") == u.get("user")


@bp.route("/api/jobs")
def api_jobs():
    """?active=1 doar cele in asteptare/rulare; ?limit=. Adminul vede tot."""
    u = current_user() or {}
    who = None if u.get("role") == "admin" else u.get("user")
    return jsonify({"joburi": jobs.lista(who=who, limit=min(200, request.args.get(
        "limit", 50, type=int)), doar_active=bool(request.args.get("active")))})


@bp.route("/api/jobs/<jid>", methods=["GET", "DELETE"])
def api_job(jid):
    j = jobs.get(jid)
    if not j or not _al_meu(j):
        return jsonify({"error": "job inexistent"}), 404
    if request.method == "DELETE":
        return jsonify({"ok": jobs.cancel(jid), **(jobs.get(jid) or {})})
    return jsonify(j)


@bp.route("/api/jobs/<jid>/fisier")
def api_job_fisier(jid):
    j = jobs.get(jid)
    if not j or not _al_meu(j):
        return jsonify({"error": "job inexistent"}), 404
    f = jobs.cale_fisier(jid)
    if not f:
        return jsonify({"error": "fișierul nu (mai) există — joburile se păstrează o ora"}), 404
    cale, nume, mime = f
    return send_file(cale, mimetype=mime or "application/octet-stream",
                     as_attachment=True, download_name=nume or "rezultat")
