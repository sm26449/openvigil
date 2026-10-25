#!/usr/bin/env python3
"""Blueprint: operare ca Synology — modul acasa/plecat/noapte si harta proprietatii.

Modul: GET pentru oricine logat, POST pentru admin si operator (guard-ul din server lasa
operatorul pe /api/mod). Harta: GET pentru oricine (filtrata pe camerele permise),
modificarile doar admin. Imaginea planului vine in corpul cererii (fetch cu Blob),
nu multipart — simplu si fara biblioteci.
"""
from flask import Blueprint, jsonify, request, send_file

import audit
import harta
import mod
from server import _visible_ids, current_user

bp = Blueprint("operare", __name__)


@bp.route("/api/mod", methods=["GET", "POST"])
def api_mod():
    if request.method == "GET":
        return jsonify({**mod.get(), "moduri": list(mod.MODURI), "nume": mod.MOD_RO})
    b = request.get_json(silent=True) or {}
    u = current_user() or {}
    try:
        st = mod.set(str(b.get("mod") or ""), cine=u.get("user"), sursa="ui")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **st})


@bp.route("/api/harta")
def api_harta():
    return jsonify({"planuri": harta.lista(_visible_ids())})


@bp.route("/api/harta", methods=["POST"])
def api_harta_save():
    """{plan_nou: nume} | {id, nume} | {id, camere: {cam: {x,y,dir,fov} | null}}"""
    b = request.get_json(silent=True) or {}
    try:
        if b.get("plan_nou") is not None:
            p = harta.plan_nou(str(b["plan_nou"]))
            audit.log("plan de harta creat", p["nume"])
        elif b.get("id") and b.get("camere") is not None:
            p = harta.aseaza(str(b["id"]), b["camere"])
            audit.log("camere asezate pe harta", p["nume"], {"camere": list(b["camere"].keys())})
        elif b.get("id") and b.get("nume") is not None:
            p = harta.redenumeste(str(b["id"]), str(b["nume"]))
            audit.log("plan de harta redenumit", p["nume"])
        else:
            return jsonify({"error": "nimic de facut"}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, "plan": {"id": p["id"], "nume": p.get("nume"),
                                         "camere": p.get("camere") or {},
                                         "imagine": harta.imagine(p["id"]) is not None}})


@bp.route("/api/harta/<pid>", methods=["DELETE"])
def api_harta_delete(pid):
    try:
        harta.sterge(pid)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    audit.log("plan de harta sters", pid)
    return jsonify({"ok": True})


@bp.route("/api/harta/<pid>/imagine", methods=["GET", "POST"])
def api_harta_imagine(pid):
    if request.method == "GET":
        cale = harta.imagine(pid)
        if not cale:
            return jsonify({"error": "planul nu are imagine"}), 404
        return send_file(cale, max_age=0, conditional=True)
    try:
        harta.salveaza_imagine(pid, request.get_data(), request.content_type)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("imagine de plan incarcata", pid, {"octeti": request.content_length})
    return jsonify({"ok": True})
