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
from i18n import t

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
        (t("o persoana necunoscuta") if n <= 1 else t("{n} persoane simultan", n=n))
    sec = int(e["last"] - e["t0"])
    durata = f"{sec}s" if sec < 90 else t("{n} min", n=round(sec / 60))
    parti = [f"{cine} · {durata}"]
    if fapta:
        parti.append(t(fapta))
    if e["zone"]:
        parti.append(t("zone: {lista}", lista=", ".join(sorted(e["zone"]))))
    if e["directii"]:
        parti.append(t("directie: {lista}", lista=", ".join(sorted(e["directii"]))))
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
    date = dict(text=_text(e, nume, semnalmente, fapta),
                inceput=time.strftime("%H:%M:%S", time.localtime(t0)),
                durata_sec=int(t1 - t0),
                persoane_max=e["max_simultan"],
                aparitii=e["aparitii"],
                zone=sorted(e["zone"]),
                directii=sorted(e["directii"]),
                nume=nume,
                comportament=fapta,
                traseu=_subesantion(e["traseu"]) if e.get("traseu") else None)
    if _llm_pornit():
        # modelul de limbaj dureaza secunde: nu in firul de urmarire; emitem din firul lui
        import threading
        threading.Thread(target=_emite_cu_descriere, args=(cam, date, t0, t1),
                         daemon=True, name="episod-llm").start()
    else:
        _emite(cam, date)


def _emite(cam, date):
    try:
        import hooks
        hooks.emit("episod", cam, **date)
    except Exception as ex:
        logare.info(f"[episoade] emit cam{cam}: {str(ex)[:60]}")


def _llm_pornit():
    try:
        import settings
        return bool(settings.get("episod_llm"))
    except Exception:
        return False


def _nume_cam(cam):
    try:
        import notify
        return notify._cam_name(cam)
    except Exception:
        return f"cam {cam}"


def _vazute_in_scena(cam, t0, t1):
    """Ce a mai etichetat „Invata scena” pe camera in fereastra episodului (fara vreme)."""
    try:
        import jurnal
        import scena_clasa
        neutre = scena_clasa.NEUTRE
        return sorted({e.get("eticheta") for e in jurnal.query(limit=200, tip="scena_candidat", cam=cam)
                       if t0 <= _epoch(e.get("t")) <= t1 and e.get("eticheta") not in neutre} - {None})
    except Exception:
        return []


def prompt_descriere(cam_nume, date, limba, vazute=()):
    """Faptele episodului -> cererea catre modelul de limbaj: o singura propozitie, in limba
    interfetei, fara inventii (doar ce e in fapte)."""
    en = limba == "en"
    et = (lambda ro, eng: eng if en else ro)  # noqa: E731
    fapte = [f"{et('camera', 'camera')}: {cam_nume}", f"{et('ora', 'time')}: {date.get('inceput')}",
             f"{et('durata', 'duration')}: {date.get('durata_sec')} s",
             f"{et('persoane simultan', 'people at once')}: {date.get('persoane_max')}",
             f"{et('aparitii', 'appearances')}: {date.get('aparitii')}"]
    if date.get("nume"):
        fapte.append(et("recunoscuti", "recognised") + ": " + ", ".join(date["nume"]))
    if date.get("zone"):
        fapte.append(et("zone", "zones") + ": " + ", ".join(date["zone"]))
    if date.get("directii"):
        fapte.append(et("directii", "directions") + ": " + ", ".join(date["directii"]))
    if date.get("comportament"):
        fapte.append(et("comportament", "behaviour") + ": " + t(str(date["comportament"]), limba))
    if vazute:
        fapte.append(et("vazute si", "also seen") + ": " + ", ".join(t(v, limba) for v in vazute))
    fapte.append(et("rezumat-sablon", "template summary") + ": " + str(date.get("text") or ""))
    if en:
        return ("Write one sentence in English (at most 90 characters) that summarises this "
                "surveillance episode for the owner. Use only the facts below, no assumptions, "
                "no preamble.\n" + "\n".join(fapte))
    return ("Scrie o singură propoziție în limba română (cel mult 90 de caractere) care rezumă "
            "acest episod de supraveghere pentru proprietar. Folosește doar faptele de mai jos, "
            "fără presupuneri, fără introducere.\n" + "\n".join(fapte))


def scurteaza(txt, maxim=118):
    """Jurnalul tine valorile pana in 120 de caractere: taiem la cuvant, cu „…”."""
    txt = " ".join((txt or "").split()).strip(" \"„”")
    if len(txt) <= maxim:
        return txt
    taiat = txt[:maxim - 1].rsplit(" ", 1)[0]
    return taiat.rstrip(",;:") + "…"


def _emite_cu_descriere(cam, date, t0, t1):
    try:
        import llm
        from i18n import limba_curenta
        txt = llm.completeaza(prompt_descriere(_nume_cam(cam), date, limba_curenta(), _vazute_in_scena(cam, t0, t1)),
                              max_tokens=80, timeout=90)
        txt = scurteaza(txt)
        if len(txt) > 10:
            date = {**date, "text_sablon": date["text"], "text": txt}
    except Exception as ex:
        logare.info(f"[episoade] descriere llm cam{cam}: {str(ex)[:80]}")
    _emite(cam, date)


def snapshot():
    """Episoadele deschise acum: {cam: {de_la, persoane, zone}} — pentru stare/debug."""
    now = time.time()
    return {cam: {"de_secunde": int(now - e["t0"]), "persoane": e["max_simultan"],
                  "zone": sorted(e["zone"])}
            for cam, e in _ep.items()}
