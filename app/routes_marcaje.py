#!/usr/bin/env python3
"""Blueprint: marcaje (bookmarks) pe inregistrari.

GET  /api/marcaje?cam=&day=&limit=   lista (viewer/operator: doar camerele lor)
POST /api/marcaje {cam, start, end?, eticheta, nota}   (admin + operator, pe camera lui)
PATCH/DELETE /api/marcaje/<id>
Guard-ul global (server.before_request) face autentificarea si CSRF-ul; aici doar
restrictia pe camere, pentru ca marcajul numeste camera in corp, nu in ?channel=.
"""
from flask import Blueprint, jsonify, request

import audit
import marcaje
from i18n import t
from server import _visible_ids, current_user

bp = Blueprint("marcaje", __name__)


def _permis(cam, vis):
    try:
        return vis is None or int(cam) in vis
    except (TypeError, ValueError):
        return False


@bp.route("/api/marcaje", methods=["GET", "POST"])
def api_marcaje():
    vis = _visible_ids()
    if request.method == "GET":
        return jsonify({"marcaje": marcaje.lista(
            cam=request.args.get("cam", type=int), day=request.args.get("day") or None,
            limit=min(2000, request.args.get("limit", 500, type=int)), visible=vis)})
    b = request.get_json(silent=True) or {}
    if not _permis(b.get("cam"), vis):
        return jsonify({"error": t("nu ai acces la această camera")}), 403
    try:
        m = marcaje.create(b.get("cam"), b.get("start"), end=b.get("end") or None,
                           eticheta=b.get("eticheta", ""), nota=b.get("nota", ""),
                           who=(current_user() or {}).get("user"))
    except (ValueError, TypeError) as e:
        return jsonify({"error": t("marcaj invalid: {e}", e=str(e)[:80])}), 400
    audit.log("marcaj adăugat", f"cam {m['cam']} {m['t']} {m['eticheta']}")
    return jsonify(m)


@bp.route("/api/marcaje/<mid>", methods=["PATCH", "DELETE"])
def api_marcaj(mid):
    m = marcaje.get(mid)
    if not m:
        return jsonify({"error": t("marcaj inexistent")}), 404
    if not _permis(m["cam"], _visible_ids()):
        return jsonify({"error": t("nu ai acces la această camera")}), 403
    if request.method == "DELETE":
        marcaje.delete(mid)
        audit.log("marcaj sters", f"cam {m['cam']} {m['t']}")
        return jsonify({"ok": True})
    b = request.get_json(silent=True) or {}
    try:
        m = marcaje.update(mid, eticheta=b.get("eticheta"), nota=b.get("nota"),
                           end=b.get("end"))
    except (ValueError, TypeError) as e:
        return jsonify({"error": t("marcaj invalid: {e}", e=str(e)[:80])}), 400
    return jsonify(m)
