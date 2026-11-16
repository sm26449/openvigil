#!/usr/bin/env python3
"""„Cât ai lipsit” — ce s-a întâmplat de la ultima privire, în câteva rânduri.

Prima pagină după o absență nu trebuie să fie o listă de sute de incidente, ci un rezumat:
câte au fost (și câte critice), grupate ca în triaj (aceeași cameră, același tip), cine a
venit (fețe și numere cunoscute) și ce camere au căzut și n-au revenit. Interfața îl cere
cu momentul ultimei vizite pe acel dispozitiv (audit UX 2026-11-14, UX-G).
"""
import time
from collections import Counter

import incidents
import rules

MAX_ZILE = 7          # o absenta mai lunga se rezuma tot pe ultima saptamana
MAX_GRUPURI = 5
GRUP_GOL = 1800       # acelasi prag ca gruparea din interfata (40-evenimente.js)


def _sec(e):
    try:
        return time.mktime(time.strptime(e["t"], "%Y-%m-%d %H:%M:%S"))
    except (KeyError, ValueError, TypeError):
        return 0.0


def _grupeaza(evs):
    """Incidentele (cele mai noi primele) -> grupuri pe (camera, tip), rupte la pauze de 30 min."""
    deschis, grupuri = {}, []
    for e in sorted(evs, key=_sec, reverse=True):
        k = (e.get("cam"), e.get("tip"))
        g = deschis.get(k)
        if g and _sec(g[-1]) - _sec(e) <= GRUP_GOL:
            g.append(e)
        else:
            g = [e]
            deschis[k] = g
            grupuri.append(g)
    return grupuri


def rezumat(de_la, visible=None, acum=None):
    acum = acum or time.time()
    de_la = max(float(de_la), acum - MAX_ZILE * 86400)
    evs = rules.journal(limit=5000, min_sev="atentie", since=de_la, cams=visible)
    inc = []
    for e in evs:
        e["sev"] = rules.event_severity(e)
        if rules.SEV_RANK.get(e["sev"], 0) >= rules.SEV_RANK["atentie"]:
            inc.append(e)
    incidents.annotate(inc)
    grupuri = _grupeaza(inc)
    # ordinea: critice intai, apoi cele cu mai multe netratate, apoi cele mai noi
    grupuri.sort(key=lambda g: (-max(rules.SEV_RANK.get(x["sev"], 0) for x in g),
                                -sum(1 for x in g if x.get("stare") == "nou"), -_sec(g[0])))
    rep = [g[0] for g in grupuri[:MAX_GRUPURI]]
    incidents.adauga_poze(rep)
    top = [{"tip": g[0].get("tip"), "cam": g[0].get("cam"), "n": len(g),
            "netratate": sum(1 for x in g if x.get("stare") == "nou"),
            "sev": max((x["sev"] for x in g), key=lambda s: rules.SEV_RANK.get(s, 0)),
            "de_la": g[-1]["t"], "pana_la": g[0]["t"], "id": g[0].get("id"),
            "text": g[0].get("text") or g[0].get("nume") or g[0].get("numar") or g[0].get("eticheta") or "",
            "poza": g[0].get("poza")} for g in grupuri[:MAX_GRUPURI]]

    def _cine(tip, cheie):
        lst = rules.journal(limit=2000, tip=tip, since=de_la, cams=visible)
        return [{"nume": n, "n": c} for n, c in Counter(e.get(cheie) for e in lst if e.get(cheie)).most_common(8)]

    cazute = {}
    for e in sorted(rules.journal(limit=500, tip="camera_offline", since=de_la, cams=visible)
                    + rules.journal(limit=500, tip="camera_online", since=de_la, cams=visible), key=_sec):
        cazute[e.get("cam")] = e.get("tip") == "camera_offline"
    # starea de ACUM a watchdog-ului are ultimul cuvant: o camera cazuta INAINTE de ultima
    # vizita si inca jos n-are tranzitie in interval, dar trebuie sa apara (audit F5)
    try:
        import notify
        for cid, ok in dict(notify._states).items():
            if isinstance(cid, int) and ok is not None and (visible is None or cid in visible):
                cazute[cid] = not ok
    except Exception:
        pass
    return {
        "de_la": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(de_la)),
        "ore": round((acum - de_la) / 3600, 1),
        "incidente": len(inc),
        "critice": sum(1 for e in inc if e["sev"] == "critic"),
        "netratate": sum(1 for e in inc if e.get("stare") == "nou"),
        "grupuri": top,
        "grupuri_total": len(grupuri),
        "persoane": _cine("persoana", "nume"),
        "vehicule": _cine("numar", "nume"),
        "camere_cazute": sorted(c for c, jos in cazute.items() if jos and c is not None),
    }
