#!/usr/bin/env python3
"""Panoul de incidente — triajul in timp real pentru un operator.

Un "incident" e un eveniment cu severitate >= atentie. Jurnalul le are pe toate;
aici le scoatem pe cele care cer decizie, prioritizate (critic inaintea atentiei,
apoi cronologic), cu starea de tratare atasata.

Fluxul operatorului: un incident nou apare -> il vede in panou (cu clip) -> apasa
"confirmat" (l-am tratat) sau "fals" (nu conta). Starea se tine in data/incidents.json
(id eveniment -> {stare, cine, cand}), separat de jurnalul append-only. Asa doi
operatori nu trateaza acelasi lucru de doua ori si nimic nu se pierde.
"""
import os
import threading
import time

import store
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACK_FILE = os.path.join(ROOT, "data", "incidents.json")
STARI = ("nou", "confirmat", "fals")
ACK_KEEP_DAYS = 30
_lock = threading.Lock()


def _load():
    return store.load_json(ACK_FILE, {}) or {}


def _write(data):
    store.atomic_write_json(ACK_FILE, data, mode=0o600)


def _save(data):
    with _lock:
        _write(data)


def set_state(event_id, stare, who=None):
    """Marcheaza un incident: confirmat / fals / nou (dez-confirmare)."""
    if stare not in STARI:
        raise ValueError(t("stare necunoscută: {stare}", stare=stare))
    with _lock:      # RMW sub lock: doi operatori (sau resolve_recent + UI) nu pierd ACK-uri
        data = _load()
        if stare == "nou":
            data.pop(event_id, None)
        else:
            data[event_id] = {"stare": stare, "cine": who,
                              "cand": time.strftime("%Y-%m-%d %H:%M:%S")}
        # curatare: starile mai vechi de ACK_KEEP_DAYS se uita (jurnalul le taie oricum)
        taiere = time.strftime("%Y-%m-%d",
                               time.localtime(time.time() - ACK_KEEP_DAYS * 86400))
        data = {k: v for k, v in data.items()
                if v.get("cand", "9999")[:10] >= taiere or k == event_id}
        _write(data)
    return data.get(event_id, {"stare": "nou"})


def resolve_recent(cam_id, tip, who="auto"):
    """Marcheaza rezolvate incidentele NETRATATE de un tip pentru o camera — pentru
    auto-rezolvare (ex. camera_offline cand camera revine online, ca un blip de retea
    sa nu lase incidente stale in panou). Intoarce cate a rezolvat."""
    import rules
    ack = _load()
    n = 0
    for e in rules.journal(limit=400, tip=tip, cam=cam_id):
        if e.get("id") and not ack.get(e["id"]):
            set_state(e["id"], "confirmat", who=who)
            n += 1
    return n


def annotate(events):
    """Adauga fiecarui eveniment starea lui de tratare (implicit 'nou')."""
    ack = _load()
    for e in events:
        st = ack.get(e.get("id"))
        e["stare"] = st["stare"] if st else "nou"
        if st:
            e["tratat_de"] = st.get("cine")
    return events


def feed(limit=60, min_sev="atentie", doar_noi=False, visible=None):
    """Incidentele recente, prioritizate: critic inaintea atentiei, apoi cele mai noi.
    visible = None (tot) sau multime de camere permise (operator restrans)."""
    import rules
    prag = rules.SEV_RANK.get(min_sev, 0)
    evs = []
    # filtram fluxul de info (VMD ~15k/zi) DIN START prin min_sev, altfel cele 600
    # evenimente brute sunt inecate de VMD si incidentele reale ies din fereastra.
    # Apoi recalculam severitatea (retroactiv) si re-verificam pragul.
    for e in rules.journal(limit=800, min_sev=min_sev):
        e["sev"] = rules.event_severity(e)   # recalcul retroactiv (rafinarea logicii)
        if rules.SEV_RANK.get(e["sev"], 0) < prag:
            continue
        if visible is not None and e.get("cam") not in visible:
            continue
        evs.append(e)
    annotate(evs)
    if doar_noi:
        evs = [e for e in evs if e["stare"] == "nou"]
    # sort stabil in doua trepte: intai timp descrescator, apoi severitate
    # descrescator -> critic sus, iar in cadrul aceleiasi severitati cele mai noi primele
    evs.sort(key=lambda e: e.get("t", ""), reverse=True)
    evs.sort(key=lambda e: rules.SEV_RANK.get(e.get("sev", "info"), 0), reverse=True)
    return evs[:limit]


def counts(visible=None):
    """{noi, critic_noi} — pentru badge-ul de pe tab si alarma."""
    fresh = feed(limit=200, doar_noi=True, visible=visible)
    return {"noi": len(fresh),
            "critic_noi": sum(1 for e in fresh if e.get("sev") == "critic")}
