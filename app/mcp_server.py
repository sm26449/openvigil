#!/usr/bin/env python3
"""Server MCP (Model Context Protocol) peste sistem, pentru un asistent (Claude, un agent
Node-RED, orice client MCP): intreaba ce s-a intamplat, cauta in inregistrari, cere o poza,
vede recomandarile, comuta modul.

Transport: Streamable HTTP, varianta simpla — `POST /mcp` cu un mesaj JSON-RPC 2.0 si
raspunsul tot JSON (fara flux SSE; clientii accepta ambele). Autentificare cu jetonul API
(`X-Token` sau `Authorization: Bearer`), ca restul API-ului de automatizari. Pentru clientii
care vorbesc doar stdio (Claude Desktop), scripts/openvigil_mcp.py e puntea.

Fara dependente: protocolul e cateva metode (initialize, ping, tools/list, tools/call), iar
uneltele sunt functii pure peste modulele existente. Nicio unealta nu declanseaza sirena sau
nu sterge ceva: cel mult comuta modul (acasa/plecat/noapte), ca si API-ul v1.
"""
import base64
import json

import logare
import settings
from i18n import t

PROTOCOL = "2025-06-18"
MAX_LIMITA = 200


def _versiune():
    try:
        import config as C
        return str(getattr(C, "VERSIUNE", "dev"))
    except Exception:
        return "dev"


# ---- uneltele ----
def _camere(_a):
    from server import load_ext
    return [{"id": c["id"], "nume": c["name"], "grup": c.get("group"),
             "poate": sorted(k for k, v in (c.get("features") or {}).items() if v)}
            for c in load_ext()]


def _nume_camere():
    try:
        return {c["id"]: c["nume"] for c in _camere({})}
    except Exception:
        return {}


def _stare(_a):
    import ai_client
    import audio_level
    import continuity
    import detect
    det = (ai_client.detect_state() or {}).get("detect") if ai_client.remote() else None
    if det is None:
        det = detect.snapshot()
    return {"camere": continuity.live(),
            "detectii_active": {k: v["labels"] for k, v in det.items() if v.get("labels")},
            "spatiu": continuity.forecast(), "zgomot": audio_level.snapshot(),
            "mod": __import__("mod").get(), "versiune": _versiune()}


def _limita(a, implicit=50):
    try:
        return max(1, min(MAX_LIMITA, int(a.get("limita") or implicit)))
    except (TypeError, ValueError):
        return implicit


def _evenimente(a):
    import jurnal
    ev = jurnal.query(limit=_limita(a), tip=a.get("tip") or None,
                      cam=a.get("camera") if a.get("camera") is not None else None,
                      day=a.get("zi") or None, text=a.get("text") or None,
                      min_sev=a.get("severitate_minima") or None)
    nume = _nume_camere()
    for e in ev:
        if e.get("cam") in nume:
            e["camera_nume"] = nume[e["cam"]]
        e.pop("traseu", None)
    return ev


def _episoade(a):
    return _evenimente({**a, "tip": "episod", "limita": a.get("limita") or 20})


def _scena(a):
    import jurnal
    import scena_web
    cam = int(a["camera"])
    fb = scena_web.feedback_citeste(cam)
    out = []
    for e in jurnal.query(limit=_limita(a, 20), tip="scena_candidat", cam=cam):
        out.append({k: e.get(k) for k in ("id", "t", "eticheta", "incredere", "sursa", "durata")}
                   | {"verdict": (fb.get(e["id"]) or {}).get("verdict")})
    return out


def _cauta(a):
    import ai_client
    import semantic
    q = str(a.get("intrebare") or "").strip()
    if not q:
        raise ValueError(t("scrie intrebarea"))
    zile = max(1, min(365, int(a.get("zile") or 7)))
    lim = _limita(a, 10)
    rez = ai_client.semantic_search(q, zile, lim, None) if ai_client.remote() else None
    if rez is None:
        rez = semantic.search(q, days=zile, limit=lim)
    return rez


def _intreaba(a):
    import ai_client
    q = str(a.get("intrebare") or "").strip()
    if not q:
        raise ValueError(t("scrie intrebarea"))
    nume = _nume_camere()
    cam = a.get("camera")
    d = ai_client.ask(q, None, nume, cam) if ai_client.remote() else None
    if d is None:
        import ask
        d = ask.answer(q, visible=None, cam_names=nume, cam=cam)
    return d


def _poza(a):
    import requests
    import config as C
    cam = int(a["camera"])
    r = requests.get(f"{C.GO2RTC_API}/api/frame.jpeg", params={"src": f"cam{cam}"}, timeout=10)
    if not (r.ok and r.content):
        raise RuntimeError(t("nu am putut lua cadrul"))
    return {"_imagine": base64.b64encode(r.content).decode(), "mime": "image/jpeg"}


def _recomandari(a):
    import recomandari
    return recomandari.pentru_camera(int(a["camera"]))


def _mod(a):
    import mod
    if a.get("mod"):
        st = mod.set(str(a["mod"]), cine="mcp", sursa="api")
        import audit
        audit.log("api", "mod", {"mod": st["mod"], "prin": "mcp"})
        return st
    return mod.get()


UNELTE = {
    "camere": (_camere, t("Lista camerelor, cu ce stie fiecare sa faca."), {"type": "object", "properties": {}}),
    "stare": (_stare, t("Starea sistemului: cine inregistreaza, detectii active, spatiu, zgomot, mod."),
              {"type": "object", "properties": {}}),
    "evenimente": (_evenimente, t("Evenimentele din jurnal, cele mai noi primele: detectii, alarme, persoane, episoade, candidati."),
                   {"type": "object", "properties": {
                       "tip": {"type": "string", "description": "detectie | alarma_camera | persoana | episod | scena_candidat | descriere | ..."},
                       "camera": {"type": "integer"}, "zi": {"type": "string", "description": "YYYY-MM-DD"},
                       "text": {"type": "string", "description": "fragment de text cautat in eveniment"},
                       "severitate_minima": {"type": "string", "enum": ["atentie", "critic"]},
                       "limita": {"type": "integer"}}}),
    "episoade": (_episoade, t("Episoadele de prezenta (cine, cat, incotro), cu descrierea lor."),
                 {"type": "object", "properties": {"camera": {"type": "integer"}, "zi": {"type": "string"}, "limita": {"type": "integer"}}}),
    "scena": (_scena, t("Candidatii „ceva nou” ai unei camere, cu eticheta propusa si verdictul omului."),
              {"type": "object", "properties": {"camera": {"type": "integer"}, "limita": {"type": "integer"}}, "required": ["camera"]}),
    "cauta": (_cauta, t("Cautare vizuala in pozele de detectie dupa o descriere libera (ex. „masina de gunoi”)."),
              {"type": "object", "properties": {"intrebare": {"type": "string"}, "zile": {"type": "integer"}, "limita": {"type": "integer"}}, "required": ["intrebare"]}),
    "intreaba": (_intreaba, t("O intrebare in limbaj natural („cand a venit masina de gunoi?”), cu raspuns si dovezi."),
                 {"type": "object", "properties": {"intrebare": {"type": "string"}, "camera": {"type": "integer"}}, "required": ["intrebare"]}),
    "poza": (_poza, t("Cadrul curent al unei camere (JPEG)."),
             {"type": "object", "properties": {"camera": {"type": "integer"}}, "required": ["camera"]}),
    "recomandari": (_recomandari, t("Ce ar imbunatati detectia pe o camera, cu actiuni aplicabile din interfata."),
                    {"type": "object", "properties": {"camera": {"type": "integer"}}, "required": ["camera"]}),
    "mod": (_mod, t("Modul casei: citeste, sau comuta cu `mod` = acasa | plecat | noapte."),
            {"type": "object", "properties": {"mod": {"type": "string", "enum": ["acasa", "plecat", "noapte"]}}}),
}


def lista_unelte():
    return [{"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in UNELTE.items()]


def apeleaza(nume, argumente):
    """Rezultatul MCP al unei unelte: {content: [...], isError}."""
    if nume not in UNELTE:
        return {"content": [{"type": "text", "text": t("unealtă necunoscută: {nume}", nume=nume)}], "isError": True}
    fn = UNELTE[nume][0]
    try:
        rez = fn(argumente or {})
    except (ValueError, KeyError, TypeError) as e:
        return {"content": [{"type": "text", "text": t("argumente greșite: {e}", e=str(e)[:120])}], "isError": True}
    except Exception as e:
        logare.info(f"[mcp] {nume}: {str(e)[:120]}")
        return {"content": [{"type": "text", "text": str(e)[:200]}], "isError": True}
    if isinstance(rez, dict) and "_imagine" in rez:
        return {"content": [{"type": "image", "data": rez["_imagine"], "mimeType": rez.get("mime", "image/jpeg")}]}
    return {"content": [{"type": "text", "text": json.dumps(rez, ensure_ascii=False, default=str)}],
            "structuredContent": rez if isinstance(rez, dict) else {"rezultat": rez}}


# ---- JSON-RPC ----
def _eroare(id_, cod, mesaj):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": cod, "message": mesaj}}


def trateaza(mesaj):
    """Un mesaj JSON-RPC -> raspunsul (dict) sau None pentru notificari."""
    if not isinstance(mesaj, dict) or mesaj.get("jsonrpc") != "2.0" or not mesaj.get("method"):
        return _eroare(mesaj.get("id") if isinstance(mesaj, dict) else None, -32600, "cerere invalida")
    metoda, id_, p = mesaj["method"], mesaj.get("id"), mesaj.get("params") or {}
    if metoda.startswith("notifications/"):
        return None
    if metoda == "initialize":
        return {"jsonrpc": "2.0", "id": id_, "result": {
            "protocolVersion": p.get("protocolVersion") or PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "openvigil", "version": _versiune()},
            "instructions": t("Supraveghere video: camere, evenimente, episoade, cautare vizuala, poze, recomandari, modul casei. Orele sunt locale.")}}
    if metoda == "ping":
        return {"jsonrpc": "2.0", "id": id_, "result": {}}
    if metoda == "tools/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"tools": lista_unelte()}}
    if metoda == "tools/call":
        return {"jsonrpc": "2.0", "id": id_, "result": apeleaza(p.get("name"), p.get("arguments") or {})}
    if metoda in ("resources/list", "resources/templates/list"):
        return {"jsonrpc": "2.0", "id": id_, "result": {"resources": [], "resourceTemplates": []}}
    if metoda == "prompts/list":
        return {"jsonrpc": "2.0", "id": id_, "result": {"prompts": []}}
    return _eroare(id_, -32601, f"metoda necunoscuta: {metoda}")


def trateaza_corp(corp):
    """Corpul unui POST (un mesaj sau o lista) -> (raspuns JSON sau None, cod HTTP)."""
    if isinstance(corp, list):
        out = [r for r in (trateaza(m) for m in corp) if r is not None]
        return (out or None), (200 if out else 202)
    r = trateaza(corp)
    return r, (200 if r is not None else 202)


def activ():
    return bool(settings.get("api_token"))
