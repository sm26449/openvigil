#!/usr/bin/env python3
"""Episoade de persoana (E2): detectiile-cadre devin povestea unei prezente.

"Avem pe alee om — unul sau mai multi? cat au stat? incotro au plecat?" — la
intrebarile astea nu raspunde un sir de evenimente "detectie person", ci UN
episod: intervalul in care macar o persoana a fost in cadrul camerei, cu tot
ce s-a aflat intre timp (numele de la Face ID, semnalmentele de la VLM, zonele
UI prin care a trecut, directiile, cate persoane simultan).

Hranit din bucla de urmarire (un singur fir per proces — fara lock):
  cadru()        — centrele persoanelor din fiecare cadru (deschide/prelungeste);
  track_inchis() — un track de persoana s-a incheiat (directia, inca o aparitie);
  tick()         — fara nicio persoana de GAP secunde -> episodul se INCHIDE:
                   abia acum se string numele/semnalmentele din jurnale (FS) si
                   pleaca UN eveniment "episod" cu rezumatul in romana.
"""
import time

import ai_analytics
import logare

GAP = 45           # secunde fara nicio persoana -> episodul s-a incheiat
NUME_FEREASTRA = 30  # numele recunoscute cu putin inainte de episod tot ale lui sunt
TRASEU_PUNCTE = 40   # cate puncte din traiectorie pastram in eveniment (mini-harta)

_ep = {}           # cam -> episodul activ


def _drum(trail):
    """Lungimea drumului parcurs (suma segmentelor, coordonate normalizate)."""
    return sum(((trail[i][0] - trail[i - 1][0]) ** 2
                + (trail[i][1] - trail[i - 1][1]) ** 2) ** 0.5
               for i in range(1, len(trail)))


def _net(trail):
    """Deplasarea neta (distanta cap-coada)."""
    return ((trail[-1][0] - trail[0][0]) ** 2 + (trail[-1][1] - trail[0][1]) ** 2) ** 0.5


def comportament(durata, trail, dwell):
    """"Ce a facut?" — o fraza dedusa PUR din traiectorie si din timpul petrecut
    in zonele UI. Ordinea: zona bate restul (cel mai concret), apoi forma drumului."""
    if dwell:
        zona, sec = max(dwell.items(), key=lambda kv: kv[1])
        if sec >= 30:
            cat = f"{int(sec)}s" if sec < 90 else f"{round(sec / 60)} min"
            return f"a stat {cat} la {zona}"
    if not trail or len(trail) < 3:
        return "a trecut prin cadru" if durata < 30 else None
    drum, net = _drum(trail), _net(trail)
    # "invartit" = drum LUNG cu deplasare neta mica; fara pragul de drum, tremuratul
    # pe loc (jitter de biti pe un om static) ar parea plimbare
    if durata >= 60 and drum >= 0.5 and drum / max(net, 1e-6) >= 3:
        return "s-a invartit prin cadru"
    if durata >= 30 and net < 0.15:
        return "a zabovit pe loc"
    return "a trecut prin cadru"


def _subesantion(trail, n=TRASEU_PUNCTE):
    """Cel mult n puncte, forma pastrata (capetele raman mereu). Doua zecimale
    ajung: pe mini-harta de 64px inseamna sub un pixel, iar jurnalul ramane suplu."""
    if len(trail) > n:
        pas = (len(trail) - 1) / (n - 1)
        trail = [trail[round(i * pas)] for i in range(n)]
    return [[round(p[0], 2), round(p[1], 2)] for p in trail]


def cadru(cam, centre, now):
    """Centrele normalizate ale persoanelor vazute in cadrul curent."""
    if not centre:
        return
    e = _ep.get(cam)
    if e is None:
        e = _ep[cam] = {"t0": now, "last": now, "max_simultan": 0,
                        "aparitii": 0, "directii": set(), "zone": set()}
    e["last"] = now
    e["max_simultan"] = max(e["max_simultan"], len(centre))
    for z in ai_analytics.zones(cam):
        poly = [p for p in z.get("poly", []) if len(p) == 2]
        if len(poly) < 3:
            continue
        if any(ai_analytics._point_in_poly(c, poly) for c in centre):
            e["zone"].add(z.get("name", "zona"))


def track_inchis(cam, directie, now, trail=None, durata=0.0, cine=None):
    """Un track de persoana s-a incheiat in interiorul episodului. Traiectoria lui
    hraneste "ce a facut": pastram drumul cel mai lung (reprezentativ pentru
    mini-harta) si adunam timpul petrecut in fiecare zona UI (E3). `cine` e numele
    venit din re-ID pe corp (fata ancoreaza, corpul propaga) — intra in rezumat."""
    e = _ep.get(cam)
    if e is None:
        return
    e["aparitii"] += 1
    if cine:
        e.setdefault("cine", set()).add(cine)
    if directie and directie != "stationar":
        e["directii"].add(directie)
    if not trail:
        return
    if len(trail) > len(e.get("traseu") or []):
        e["traseu"] = trail
        e["traseu_durata"] = durata
    pas = durata / max(len(trail) - 1, 1)     # ~secunde per punct esantionat
    for z in ai_analytics.zones(cam):
        poly = [p for p in z.get("poly", []) if len(p) == 2]
        if len(poly) < 3:
            continue
        inauntru = sum(1 for p in trail if ai_analytics._point_in_poly(p, poly))
        if inauntru:
            nume_z = z.get("name", "zona")
            e.setdefault("dwell", {})
            e["dwell"][nume_z] = e["dwell"].get(nume_z, 0.0) + inauntru * pas


def _epoch(t):
    try:
        return time.mktime(time.strptime(t, "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0


def _nume(cam, t0, t1):
    """Persoanele recunoscute pe camera in fereastra episodului (jurnalul Face ID)."""
    try:
        import faces
        return sorted({e["nume"] for e in faces.history(limit=80)
                       if e.get("cam") == cam
                       and t0 - NUME_FEREASTRA <= _epoch(e.get("t")) <= t1})
    except Exception:
        return []


def _semnalmente(cam, t0, t1):
    """Cea mai noua descriere VLM de persoana din fereastra (jurnalul de evenimente)."""
    try:
        import rules
        for e in rules.journal(limit=300, tip="descriere", cam=cam):
            if e.get("obiect") == "vehicul":
                continue
            if t0 <= _epoch(e.get("t")) <= t1:
                return e.get("text") or None
    except Exception:
        pass
    return None


def _text(e, nume, semnalmente, fapta=None):
    """Rezumatul in romana — UI-ul il afiseaza ca atare pe cardul de incident."""
    n = e["max_simultan"]
    cine = ", ".join(nume) if nume else \
        ("o persoana necunoscuta" if n <= 1 else f"{n} persoane simultan")
    sec = int(e["last"] - e["t0"])
    durata = f"{sec}s" if sec < 90 else f"{round(sec / 60)} min"
    parti = [f"{cine} · {durata}"]
    if fapta:
        parti.append(fapta)
    if e["zone"]:
        parti.append("zone: " + ", ".join(sorted(e["zone"])))
    if e["directii"]:
        parti.append("directie: " + ", ".join(sorted(e["directii"])))
    if semnalmente:
        parti.append(semnalmente.rstrip("."))
    return " · ".join(parti)


def tick(cam, now):
    """Inchide episodul camerei daca nu s-a mai vazut nicio persoana de GAP secunde."""
    e = _ep.get(cam)
    if e is None or now - e["last"] < GAP:
        return
    del _ep[cam]
    t0, t1 = e["t0"], e["last"]
    nume = sorted(set(_nume(cam, t0, t1)) | e.get("cine", set()))
    semnalmente = _semnalmente(cam, t0, t1)
    fapta = comportament(t1 - t0, e.get("traseu"), e.get("dwell"))
    try:
        import hooks
        hooks.emit("episod", cam,
                   text=_text(e, nume, semnalmente, fapta),
                   inceput=time.strftime("%H:%M:%S", time.localtime(t0)),
                   durata_sec=int(t1 - t0),
                   persoane_max=e["max_simultan"],
                   aparitii=e["aparitii"],
                   zone=sorted(e["zone"]),
                   directii=sorted(e["directii"]),
                   nume=nume,
                   comportament=fapta,
                   traseu=_subesantion(e["traseu"]) if e.get("traseu") else None)
    except Exception as ex:
        logare.info(f"[episoade] emit cam{cam}: {str(ex)[:60]}")


def snapshot():
    """Episoadele deschise acum: {cam: {de_la, persoane, zone}} — pentru stare/debug."""
    now = time.time()
    return {cam: {"de_secunde": int(now - e["t0"]), "persoane": e["max_simultan"],
                  "zone": sorted(e["zone"])}
            for cam, e in _ep.items()}
