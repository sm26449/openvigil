#!/usr/bin/env python3
"""Blueprint: notificari push (Web Push) — abonarea dispozitivului curent si proba.

Orice rol se poate abona (guard-ul din server lasa POST/DELETE pe /api/push/ pentru
operator si viewer): un paznic cu telefonul lui primeste incidentele de pe camerele lui.
Ce primeste fiecare decide push.py (nivel + restrictia pe camere), nu browserul.
"""
from flask import Blueprint, jsonify, request

import audit
import push
from i18n import t
from server import current_user

bp = Blueprint("push", __name__)


@bp.route("/api/push/cheie")
def api_push_cheie():
    """Cheia publica VAPID + abonamentele utilizatorului (ca interfata sa stie daca acest
    browser e deja abonat)."""
    u = current_user() or {}
    k = push.cheie_publica()
    return jsonify({"cheie": k, "activ": bool(k), "https": request.is_secure,
                    "abonamente": push.lista(u.get("user"))})


@bp.route("/api/push/abonare", methods=["POST", "DELETE"])
def api_push_abonare():
    u = current_user() or {}
    b = request.get_json(silent=True) or {}
    if request.method == "DELETE":
        ok = push.dezaboneaza(u.get("user"), str(b.get("endpoint") or ""),
                              admin=u.get("role") == "admin")
        if ok:
            audit.log("push dezabonat", u.get("user"))
        return jsonify({"ok": ok})
    try:
        ab = push.aboneaza(u.get("user"), b.get("abonament"), b.get("nivel") or "critic",
                           request.headers.get("User-Agent", ""))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("push abonat", u.get("user"), {"nivel": ab["nivel"]})
    return jsonify({"ok": True, "abonament": ab})


@bp.route("/api/push/test", methods=["POST"])
def api_push_test():
    u = current_user() or {}
    n = push.test(u.get("user"))
    if not n:
        return jsonify({"error": t("niciun dispozitiv abonat pentru contul tău")}), 400
    return jsonify({"ok": True, "trimise": n})


@bp.route("/api/push/abonamente")
def api_push_abonamente():
    """Adminul vede toate dispozitivele abonate (cine, nivel, cand)."""
    u = current_user() or {}
    return jsonify({"abonamente": push.lista(None if u.get("role") == "admin" else u.get("user"))})
