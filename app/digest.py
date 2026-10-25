#!/usr/bin/env python3
"""Rezumatul zilnic — ce s-a intamplat azi, dintr-o privire.

Un operator care preia tura sau un organ care cere o informare nu vrea sa deruleze
mii de evenimente. Vrea trei lucruri: cate incidente au fost (si care erau grave),
pe ce camere s-a miscat cel mai mult, si intre ce ore. Modulul aduna jurnalul zilei
si scoate exact atat — cu incidentele critice enumerate (ora, camera, ce), pentru ca
alea sunt titlurile zilei.

Severitatea se recalculeaza la citire (event_severity), ca rafinarile logicii sa se
aplice si zilelor trecute fara sa rescriem jurnalul.
"""
import time
from collections import Counter

import rules

# tipurile relevante pentru securitate, numarate separat in rezumat
SECURITATE = {
    "persoana_necunoscuta": "persoane necunoscute",
    "fata_falsa": "fete falsificate (anti-spoofing)",
    "numar": "numere de masina",
}


def _azi():
    return time.strftime("%Y-%m-%d")


def _det(e):
    """Descrierea scurta a unui eveniment, pentru lista de incidente."""
    return (e.get("text") or e.get("nume") or e.get("numar")
            or e.get("eticheta") or e.get("subtip") or "")


def build(day=None, visible=None):
    """Rezumatul unei zile. visible = None (tot) sau multime de camere permise."""
    day = day or _azi()
    evs = rules.journal(limit=100000, day=day)
    if visible is not None:
        evs = [e for e in evs if e.get("cam") in visible]
    for e in evs:
        e["sev"] = rules.event_severity(e)

    sev = Counter(e["sev"] for e in evs)
    tipuri = Counter(e.get("tip") for e in evs)
    camere = Counter(e.get("cam") for e in evs if e.get("cam") is not None)
    ore = Counter(e["t"][11:13] for e in evs if len(e.get("t", "")) >= 13)
    secz = {k: sum(1 for e in evs if e.get("tip") == k) for k in SECURITATE}

    # incidentele critice, deduplicate ca episoade (un eveniment repetat = un rand)
    crit_ep = rules.episodes(limit=200, day=day, min_sev="critic")
    if visible is not None:
        crit_ep = [e for e in crit_ep if e.get("cam") in visible]
    critice = [{"ora": ep["inceput"][11:16], "cam": ep.get("cam"),
                "tip": ep.get("tip"), "detaliu": ep.get("detaliu") or "",
                "de_cate_ori": ep.get("evenimente", 1)}
               for ep in crit_ep]

    times = sorted(e["t"] for e in evs if e.get("t"))
    varf = ore.most_common(1)[0] if ore else None

    return {
        "zi": day,
        "total": len(evs),
        "severitate": {"critic": sev.get("critic", 0),
                       "atentie": sev.get("atentie", 0),
                       "info": sev.get("info", 0)},
        "incidente": sev.get("critic", 0) + sev.get("atentie", 0),
        "pe_tip": dict(tipuri),
        "top_camere": camere.most_common(5),
        "securitate": secz,
        "critice": critice,
        "prima_activitate": times[0][11:16] if times else None,
        "ultima_activitate": times[-1][11:16] if times else None,
        "ora_varf": (f"{varf[0]}:00", varf[1]) if varf else None,
    }


# ---- rezumatul zilei pe Telegram (o data pe zi, la digest_ora) ----

def _nume_camere():
    try:
        from server import load_ext
        return {c["id"]: c.get("name") or f"cam {c['id']}" for c in load_ext()}
    except Exception:
        return {}


def text(day=None):
    """Mesajul de rezumat pentru Telegram (text simplu): evenimente si incidente, cine a fost
    vazut, masini necunoscute, camere cu goluri sau sabotaj, starea inregistrarii, titlurile
    zilei (criticele). Fiecare sursa e optionala — o eroare intr-una nu taie mesajul."""
    day = day or _azi()
    nume = _nume_camere()
    cn = lambda c: nume.get(c, f"cam {c}")
    d = build(day=day)
    linii = [f"📋 Rezumatul zilei {day}",
             f"{d['total']} evenimente · {d['severitate']['critic']} critice, {d['severitate']['atentie']} de atenție"]
    if d.get("prima_activitate"):
        v = d.get("ora_varf")
        linii.append(f"activitate {d['prima_activitate']}–{d['ultima_activitate']}"
                     + (f", vârf la {v[0]} ({v[1]} evenimente)" if v else ""))
    if d.get("top_camere"):
        linii.append("cele mai active: " + ", ".join(f"{cn(c)} ({n})" for c, n in d["top_camere"][:3]))
    # cine a fost vazut (Face ID)
    try:
        import faces
        vazuti = Counter(h["nume"] for h in faces.history(limit=2000) if (h.get("t") or "").startswith(day))
        if vazuti:
            linii.append("👤 văzuți: " + ", ".join(f"{n}×{k}" for n, k in vazuti.most_common(6)))
    except Exception:
        pass
    # masini necunoscute
    try:
        import plates
        kn = plates.known()
        nec = [r for r in plates.reads(limit=2000) if (r.get("t") or "").startswith(day)
               and not r.get("nume") and r.get("numar") not in kn]
        if nec:
            numere = Counter(r["numar"] for r in nec)
            linii.append("🚗 mașini necunoscute: " + ", ".join(f"{p}×{k}" for p, k in numere.most_common(5)))
    except Exception:
        pass
    # inregistrare: goluri + camere care nu scriu
    try:
        import continuity
        from server import load_ext
        goluri = []
        for cam in load_ext():
            if cam.get("record", "continuous") != "continuous":
                continue
            g = continuity.gaps(cam["id"], day)
            if g:
                goluri.append(f"{cn(cam['id'])} ({len(g)}, {round(sum(x['sec'] for x in g) / 60)} min)")
        if goluri:
            linii.append("⏸ goluri de înregistrare: " + ", ".join(goluri[:6]))
        nu_scriu = [cn(x["id"]) for x in continuity.live() if x.get("mod") == "continuous" and not x.get("scrie")]
        if nu_scriu:
            linii.append("🔴 nu înregistrează acum: " + ", ".join(nu_scriu))
    except Exception:
        pass
    # sabotaj pe camere
    try:
        import snaps
        sab = Counter(it["cam"] for it in snaps.list_day(day)
                      if it["label"] in ("shelteralarm", "tamperdetection", "scenechangedetection", "defocusdetection"))
        if sab:
            linii.append("🛡 sabotaj raportat: " + ", ".join(f"{cn(c)}×{k}" for c, k in sab.most_common(5)))
    except Exception:
        pass
    # titlurile zilei
    if d.get("critice"):
        linii.append("‼ critice:")
        for c in d["critice"][:8]:
            linii.append(f"  {c['ora']} {cn(c['cam'])}: {c['tip']}{' · ' + c['detaliu'] if c['detaliu'] else ''}"
                         + (f" ×{c['de_cate_ori']}" if c.get("de_cate_ori", 1) > 1 else ""))
    return "\n".join(linii)


def trimite(day=None, sync=False):
    """Trimite rezumatul pe canalele configurate (Telegram/email). sync=True ridica erorile."""
    import notify
    msg = text(day)
    notify.send(msg, sync=sync)
    return msg


def start():
    """Firul zilnic (nvr-web): la `digest_ora`, daca `digest_telegram` e pornit. Ca la backup:
    la pornire nu trimitem „pentru ca a trecut ora"."""
    import threading
    import settings
    import logare

    def bucla():
        ultima_zi = ""
        if time.strftime("%H:%M") >= (settings.get("digest_ora") or "21:00").strip():
            ultima_zi = time.strftime("%Y-%m-%d")
        while True:
            try:
                ora = (settings.get("digest_ora") or "21:00").strip()
                azi = time.strftime("%Y-%m-%d")
                if time.strftime("%H:%M") >= ora and ultima_zi != azi:
                    ultima_zi = azi
                    if settings.get("digest_telegram"):
                        trimite(azi)
                        logare.info("[digest] rezumatul zilei trimis")
                    elif time.localtime().tm_wday == 0:
                        # rezumatul e oprit: lunea, o proba ca drumul alertelor e deschis
                        import notify
                        notify.send("✅ NVR: canalul de notificări funcționează (probă săptămânală). "
                                    "Rezumatul zilnic e oprit; îl pornești din Setări → Notificări.")
                        logare.info("[digest] proba saptamanala de canal trimisa")
            except Exception as e:
                logare.error(f"[digest] {str(e)[:120]}")
            time.sleep(60)
    threading.Thread(target=bucla, daemon=True, name="digest-zilnic").start()
