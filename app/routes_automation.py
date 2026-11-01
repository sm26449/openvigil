#!/usr/bin/env python3
"""Blueprint: jurnal de evenimente (+CSV), webhooks, MQTT.

Al doilea grup extras din server.py (audit #8). Aceste rute folosesc doar module de
serviciu (rules, hooks, settings, mqtt, audit) — niciun helper din `server` — deci
blueprint-ul e complet independent. Guard-ul de auth (before_request global) le acopera.
"""
import time

from flask import Blueprint, Response, jsonify, request

import audit
import hooks
from i18n import t
import mqtt as mqtt_bridge
import rules
import settings

bp = Blueprint("automation", __name__)


@bp.route("/api/journal")
def api_events_journal():
    """?limit=&tip=&cam=&day=YYYY-MM-DD&text=&zile= — istoricul evenimentelor, cele mai noi
    primele; `text` cauta un fragment (fara diacritice) in orice camp: descrierile VLM, rezumatele
    episoadelor, numere, nume; `zile` = doar ultimele N zile.
    Cu ?grup=1: episoade cu inceput/sfarsit/durata in loc de evenimente brute."""
    args = dict(limit=min(1000, max(1, request.args.get("limit", 200, type=int))),
                tip=request.args.get("tip") or None,
                cam=request.args.get("cam", type=int),
                day=request.args.get("day") or None,
                min_sev=request.args.get("sev") or None)
    from server import _visible_ids
    vis = _visible_ids()
    doar_vizibile = (lambda lst: [e for e in lst if vis is None or e.get("cam") is None or e.get("cam") in vis])
    if request.args.get("grup"):
        return jsonify({"episodes": doar_vizibile(rules.episodes(**args)),
                        "types": sorted(hooks.EVENIMENTE)})
    text = (request.args.get("text") or "").strip()
    zile = request.args.get("zile", type=int)
    if text:
        args["text"] = text[:80]
    if zile:
        args["since"] = time.time() - min(365, max(1, zile)) * 86400
    return jsonify({"events": doar_vizibile(rules.journal(**args)),
                    "types": sorted(hooks.EVENIMENTE)})


@bp.route("/api/journal.csv")
def api_events_csv():
    """Jurnalul ca CSV, cu aceleasi filtre — de deschis in Excel/LibreOffice."""
    evs = rules.journal(limit=min(20000, request.args.get("limit", 5000, type=int)),
                        tip=request.args.get("tip") or None,
                        cam=request.args.get("cam", type=int),
                        day=request.args.get("day") or None)
    cols = ["t", "tip", "cam", "eticheta", "nume", "numar", "regula", "context", "motiv"]
    linii = [",".join(cols)]
    for e in evs:
        linii.append(",".join(str(e.get(c, "")).replace(",", " ") for c in cols))
    return Response("\n".join(linii) + "\n", mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename=evenimente-{time.strftime('%Y%m%d')}.csv"})


@bp.route("/api/mqtt/test", methods=["POST"])
def api_mqtt_test():
    """Salveaza setarile din body (daca vin), apoi conecteaza si publica o proba."""
    b = request.get_json(silent=True) or {}
    upd = {k: b[k] for k in ("mqtt_enabled", "mqtt_host", "mqtt_port",
                             "mqtt_user", "mqtt_pass", "mqtt_topic") if k in b}
    if upd:
        settings.update(upd)
    try:
        st = mqtt_bridge.test()
    except (ValueError, RuntimeError) as e:
        return jsonify({"error": str(e)[:120]}), 502
    audit.log("mqtt testat", st.get("broker"))
    return jsonify({"ok": True, **st})


@bp.route("/api/webhooks/test", methods=["POST"])
def api_webhook_test():
    try:
        return jsonify({"rezultat": hooks.test((request.get_json(silent=True) or {}).get("url"))})
    except Exception as e:
        return jsonify({"eroare": str(e)[:200]}), 400


@bp.route("/api/webhooks")
def api_webhooks():
    return jsonify({"evenimente": {k: t(v) for k, v in hooks.EVENIMENTE.items()}, "jurnal": hooks.jurnal()})


# ---- MCP: asistentul (Claude, un agent) peste sistem, cu jetonul API ----
@bp.route("/mcp", methods=["POST", "GET", "DELETE"])
def mcp():
    """Streamable HTTP, varianta JSON: POST cu un mesaj JSON-RPC, raspuns JSON (202 gol pentru
    notificari). GET (fluxul SSE server->client) nu e oferit: 405, clientii trec peste."""
    from server import _token_ok
    if request.method != "POST":
        return jsonify({"error": "doar POST"}), 405
    if not _token_ok():
        return jsonify({"jsonrpc": "2.0", "id": None,
                        "error": {"code": -32001, "message": t("jeton lipsa sau greșit")}}), 401
    import mcp_server
    corp = request.get_json(silent=True)
    if corp is None:
        return jsonify({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "JSON invalid"}}), 400
    r, cod = mcp_server.trateaza_corp(corp)
    if r is None:
        return ("", cod)
    return jsonify(r), cod

