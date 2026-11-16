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
from i18n import t
from server import _visible_ids, current_user

bp = Blueprint("operare", __name__)


@bp.route("/api/mod", methods=["GET", "POST"])
def api_mod():
    if request.method == "GET":
        return jsonify({**mod.get(), "moduri": list(mod.MODURI), "nume": {k: t(v) for k, v in mod.MOD_RO.items()}})
    b = request.get_json(silent=True) or {}
    u = current_user() or {}
    try:
        st = mod.set(str(b.get("mod") or ""), cine=u.get("user"), sursa="ui")
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    return jsonify({"ok": True, **st})


@bp.route("/api/mod/program", methods=["GET", "POST"])
def api_mod_program():
    """Programul saptamanal de moduri (timeline). GET: programul + numele/iconitele modurilor
    si zilele saptamanii. POST (admin): {activ, zile:{"0".."6":[{h,mod}]}} — valideaza si salveaza."""
    if request.method == "GET":
        return jsonify({**mod.program(), "moduri": list(mod.MODURI),
                        "nume": {k: t(v) for k, v in mod.MOD_RO.items()},
                        "ico": dict(mod.MOD_ICO),
                        "zile_nume": [t(z) for z in ("Luni", "Marți", "Miercuri", "Joi",
                                                     "Vineri", "Sâmbătă", "Duminică")]})
    b = request.get_json(silent=True) or {}
    u = current_user() or {}
    try:
        prg = mod.set_program(b)
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400
    audit.log("program moduri salvat", "activ" if prg["activ"] else "oprit", who=u.get("user"))
    return jsonify({"ok": True, **prg})


@bp.route("/api/harta")
def api_harta():
    return jsonify({"planuri": harta.lista(_visible_ids())})


@bp.route("/api/harta", methods=["POST"])
def api_harta_save():
    """{plan_nou: nume} | {id, nume} | {id, camere: {cam: {x,y,dir,fov,...} | null}}
    | {id, scara: {x1,y1,x2,y2,m} | null} | {id, cam, calibrare: {perechi: [[cx,cy,px,py]..]} | null}
    | {id, limita: {puncte: [[x,y]..], laturi: {i: m}} | null}"""
    b = request.get_json(silent=True) or {}
    try:
        if b.get("plan_nou") is not None:
            p = harta.plan_nou(str(b["plan_nou"]))
            audit.log("plan de harta creat", p["nume"])
        elif b.get("id") and b.get("camere") is not None:
            p = harta.aseaza(str(b["id"]), b["camere"])
            audit.log("camere asezate pe harta", p["nume"], {"camere": list(b["camere"].keys())})
        elif b.get("id") and b.get("cam") is not None and "calibrare" in b:
            perechi = (b["calibrare"] or {}).get("perechi") if b["calibrare"] is not None else None
            p = harta.seteaza_calibrare(str(b["id"]), b["cam"], perechi)
            audit.log("calibrare pe plan " + ("stearsa" if perechi is None else "setata"), p["nume"],
                      {"cam": b["cam"], "perechi": len(perechi or [])})
        elif b.get("id") and "limita" in b:
            p = harta.seteaza_limita(str(b["id"]), b["limita"])
            audit.log("limita proprietății " + ("ștearsă" if b["limita"] is None else "setată"), p["nume"],
                      {"colturi": len((b["limita"] or {}).get("puncte") or []),
                       "laturi": len((b["limita"] or {}).get("laturi") or {})})
        elif b.get("id") and "scara" in b:
            p = harta.seteaza_scara(str(b["id"]), b["scara"])
            audit.log("scara planului " + ("stearsa" if b["scara"] is None else "setata"), p["nume"],
                      {"m": (b["scara"] or {}).get("m")})
        elif b.get("id") and b.get("nume") is not None:
            p = harta.redenumeste(str(b["id"]), str(b["nume"]))
            audit.log("plan de harta redenumit", p["nume"])
        else:
            return jsonify({"error": t("nimic de făcut")}), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    # planul intreg, ca la GET (cu masura recalculata): interfata il inlocuieste direct
    plan = next((x for x in harta.lista() if x["id"] == p["id"]), None)
    return jsonify({"ok": True, "plan": plan or {"id": p["id"], "nume": p.get("nume"), "camere": p.get("camere") or {}}})


@bp.route("/api/harta/omografie", methods=["POST"])
def api_harta_omografie():
    """Proba de calibrare, fara salvare: {perechi} -> {h, eroare} (previzualizarea din UI)."""
    import omografie
    try:
        return jsonify({"ok": True, **omografie.calculeaza((request.get_json(silent=True) or {}).get("perechi"))})
    except (ValueError, TypeError) as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/harta/<pid>/live")
def api_harta_live(pid):
    """Detectiile de acum ale camerelor calibrate, ca puncte pe plan (harta le cere la cateva
    secunde doar cat e deschisa si doar daca planul are camere calibrate)."""
    from routes_system import _detect_state
    try:
        pts = harta.detectii_pe_plan(pid, _detect_state().get("detect") or {}, _visible_ids())
    except ValueError as e:
        return jsonify({"error": str(e)}), 404
    return jsonify({"puncte": pts})


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
            return jsonify({"error": t("planul nu are imagine")}), 404
        return send_file(cale, max_age=0, conditional=True)
    try:
        # limita de 8 MB INAINTE de citirea corpului (altfel se citea tot, pana la limita globala)
        if (request.content_length or 0) > harta.MAX_IMG:
            raise ValueError(t("imaginea lipsește sau depaseste 8 MB"))
        harta.salveaza_imagine(pid, request.get_data(), request.content_type)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("imagine de plan incarcata", pid, {"octeti": request.content_length})
    return jsonify({"ok": True})
