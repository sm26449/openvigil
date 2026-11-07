#!/usr/bin/env python3
"""Blueprint: probe („pastreaza ca proba") pe inregistrari.

GET  /api/probe?cam=&day=&limit=   lista (viewer/operator: doar camerele lor)
POST /api/probe {cam, start, end, motiv, nota, pana_la?}   (admin + operator, pe camera lui)
PATCH/DELETE /api/probe/<id>
Guard-ul global (server.before_request) face autentificarea si CSRF-ul; aici doar
restrictia pe camere, pentru ca proba numeste camera in corp, nu in ?channel=.
"""
from flask import Blueprint, jsonify, request

import audit
import probe
from i18n import t
from server import _visible_ids, current_user

bp = Blueprint("probe", __name__)


def _permis(cam, vis):
    try:
        return vis is None or int(cam) in vis
    except (TypeError, ValueError):
        return False


@bp.route("/api/probe", methods=["GET", "POST"])
def api_probe():
    vis = _visible_ids()
    if request.method == "GET":
        return jsonify({"probe": probe.lista(
            cam=request.args.get("cam", type=int), day=request.args.get("day") or None,
            limit=min(2000, request.args.get("limit", 500, type=int)), visible=vis)})
    b = request.get_json(silent=True) or {}
    if not _permis(b.get("cam"), vis):
        return jsonify({"error": t("nu ai acces la această camera")}), 403
    try:
        p = probe.create(b.get("cam"), b.get("start"), b.get("end"),
                         motiv=b.get("motiv", ""), nota=b.get("nota", ""),
                         pana_la=b.get("pana_la") or None,
                         who=(current_user() or {}).get("user"))
    except (ValueError, TypeError) as e:
        return jsonify({"error": t("probă invalidă: {e}", e=str(e)[:80])}), 400
    audit.log("probă adăugată", f"cam {p['cam']} {p['t']}–{p['t_end']} {p['motiv']}")
    return jsonify(p)


@bp.route("/api/probe/<pid>", methods=["PATCH", "DELETE"])
def api_proba(pid):
    p = probe.get(pid)
    if not p:
        return jsonify({"error": t("probă inexistentă")}), 404
    if not _permis(p["cam"], _visible_ids()):
        return jsonify({"error": t("nu ai acces la această camera")}), 403
    if request.method == "DELETE":
        probe.delete(pid)
        audit.log("probă ștearsă", f"cam {p['cam']} {p['t']}–{p['t_end']}")
        return jsonify({"ok": True})
    b = request.get_json(silent=True) or {}
    try:
        p = probe.update(pid, motiv=b.get("motiv"), nota=b.get("nota"),
                         pana_la=(b.get("pana_la") or None) if "pana_la" in b else "keep")
    except (ValueError, TypeError) as e:
        return jsonify({"error": t("probă invalidă: {e}", e=str(e)[:80])}), 400
    return jsonify(p)
