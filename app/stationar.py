#!/usr/bin/env python3
"""Registrul obiectelor stationare (episoade E1): parcarea e o STARE, nu un eveniment.

O masina parcata nu trebuie sa alerteze la nesfarsit ("intrus masina" la fiecare
miscare din cadru — 4 alerte pe noapte pentru propria masina, masurat 2026-09-08).
Ce conteaza sunt TRANZITIILE: a sosit (parcare_inceput) si a plecat
(parcare_sfarsit, cu durata). Cat timp obiectul e in registru:
  - detectia (detect.CamState) ii suprima raportarile (filtru "stationar" — vizibil
    in overlay, nu inghitit pe ascuns);
  - confirmarea de intrus il ignora;
  - trackerul nu-l mai numara ca "obiect" la expirare (episodul il acopera).

Cine scrie: procesul care ruleaza trackingul (nvr-ai; local in web doar cu
ai_service oprit). Persistat pe disc (data/stationar.json) ca masina parcata sa nu
re-alerteze dupa un restart. Track-ID-urile ByteTrack se schimba (ocluzii,
re-aparitii) — de aceea intrarea se ADOPTA prin suprapunere, nu prin ID.
"""
import json
import os
import threading
import time
import uuid
import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "data", "stationar.json")

VEHICULE = {"car", "truck", "bus", "motorcycle"}
MOVE_EPS = 0.03        # deplasare normalizata (|dx|+|dy|) sub care obiectul e "pe loc"
STATIONAR_MIN = 120    # secunde pe loc pana devine parcare
NEVAZUT_MAX = 600      # pierdut din vedere atatea secunde -> episodul se inchide
ADOPT_IOU = 0.30       # suprapunere peste care un track nou E aceeasi parcare
RO = {"car": "masina", "truck": "camion", "bus": "autobuz", "motorcycle": "motocicleta"}
# acordul in texte: (articol+nume, participiu) — primul camion parcat a iesit
# "o camion a parcat" (2026-09-10 19:38, cam14)
FORME = {"car": ("o masina", "plecata"), "truck": ("un camion", "plecat"),
         "bus": ("un autobuz", "plecat"), "motorcycle": ("o motocicleta", "plecata")}


def _forma(lbl):
    return FORME.get(lbl, (t("un {lbl}", lbl=lbl), "plecat"))

_reg = {}              # cam -> {sid: {lbl, box(x,y,w,h normalizat), t0, last}}
_bind = {}             # (cam, track_id) -> sid
_lock = threading.Lock()
_tick_last = 0.0
_loaded = False


def _emit(tip, cam, **date):
    try:
        import hooks
        hooks.emit(tip, cam, **date)
    except Exception as e:
        logare.info(f"[stationar] emit {tip}: {str(e)[:60]}")


def _save():
    try:
        import store
        store.atomic_write_json(FILE, {str(c): e for c, e in _reg.items()}, indent=2)
    except Exception as e:
        logare.error(f"[stationar] salvare esuata: {str(e)[:60]}")


def _load():
    """La pornire: parcarile dinainte de restart raman parcari (nu re-alerteaza)."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    try:
        with open(FILE) as f:
            raw = json.load(f)
    except (FileNotFoundError, ValueError):
        return
    now = time.time()
    for c, entries in raw.items():
        for sid, e in entries.items():
            e["last"] = now        # gratie dupa restart; tick() decide mai tarziu
            _reg.setdefault(int(c), {})[sid] = e
    n = sum(len(v) for v in _reg.values())
    if n:
        logare.info(f"[stationar] {n} obiecte stationare reluate de pe disc")


def _iou(a, b):
    ax2, ay2 = a[0] + a[2], a[1] + a[3]
    bx2, by2 = b[0] + b[2], b[1] + b[3]
    ix = max(0.0, min(ax2, bx2) - max(a[0], b[0]))
    iy = max(0.0, min(ay2, by2) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def sosire(cam, track_id, lbl, box, now=None, since=None):
    """Un track a stat pe loc destul: intra in registru. `since` = momentul cand
    s-a OPRIT (ancora) — durata parcarii se masoara de-atunci, nu de la confirmare.
    Suprapunerea cu o intrare existenta = ACEEASI parcare (track-ID nou dupa
    ocluzie/restart) -> adoptie tacuta, fara al doilea eveniment. True daca e noua."""
    now = now or time.time()
    with _lock:
        _load()
        for sid, e in _reg.get(cam, {}).items():
            if _iou(e["box"], box) >= ADOPT_IOU:
                _bind[(cam, track_id)] = sid
                e.update(box=list(box), last=now)
                return False
        sid = uuid.uuid4().hex[:8]
        _reg.setdefault(cam, {})[sid] = {"lbl": lbl, "box": list(box),
                                         "t0": since or now, "last": now}
        _bind[(cam, track_id)] = sid
        _save()
    _emit("parcare_inceput", cam, eticheta=lbl, eticheta_ro=RO.get(lbl, lbl),
          zona=[round(v, 3) for v in box],
          # momentul OPRIRII — clipul din UI tinteste aici, nu confirmarea (+2 min)
          inceput=time.strftime("%H:%M:%S", time.localtime(since or now)),
          text=t("{vehicul} a parcat", vehicul=t(_forma(lbl)[0])))
    return True


def vazut(cam, track_id, box, now=None):
    """Obiectul stationar e inca la locul lui (box-ul se improspateaza usor)."""
    with _lock:
        e = _reg.get(cam, {}).get(_bind.get((cam, track_id), ""))
        if e:
            e["box"] = list(box)
            e["last"] = now or time.time()


def plecare(cam, track_id, now=None):
    """Obiectul s-a urnit: episodul se inchide cu durata lui."""
    now = now or time.time()
    with _lock:
        sid = _bind.pop((cam, track_id), None)
        e = _reg.get(cam, {}).pop(sid, None) if sid else None
        if e:
            _save()
    if e:
        durata = round((now - e["t0"]) / 60, 1)
        _emit("parcare_sfarsit", cam, eticheta=e["lbl"],
              eticheta_ro=RO.get(e["lbl"], e["lbl"]), durata_min=durata,
              text=t("{vehicul} {plecat} dupa {durata}", vehicul=t(_forma(e['lbl'])[0]),
                     plecat=t(_forma(e['lbl'])[1]),
                     durata=(t("{n} min", n=durata) if durata < 90 else t("{n} ore", n=round(durata / 60, 1)))))
    return e


def tick(now=None):
    """Curatenie periodica: o parcare nevazuta de mult (obiectul a disparut fara
    faza de miscare — ocluzie lunga, plecare in bezna) se inchide cu nota."""
    global _tick_last
    now = now or time.time()
    if now - _tick_last < 10:
        return
    _tick_last = now
    disparute = []
    with _lock:
        _load()
        for cam, entries in _reg.items():
            for sid in list(entries):
                e = entries[sid]
                if now - e["last"] > NEVAZUT_MAX:
                    disparute.append((cam, entries.pop(sid)))
        if disparute:
            _bind.clear()      # legaturile spre intrari disparute nu mai au sens
            _save()
    for cam, e in disparute:
        # durata pana la ULTIMA vedere, nu pana acum: gratia de NEVAZUT_MAX nu e
        # timp parcat (prima inchidere reala raporta 12 min pentru o oprire de ~2)
        durata = round((e["last"] - e["t0"]) / 60, 1)
        _emit("parcare_sfarsit", cam, eticheta=e["lbl"],
              eticheta_ro=RO.get(e["lbl"], e["lbl"]), durata_min=durata,
              nota=t("pierdut din vedere"),
              # clipul tinteste ULTIMA vedere, nu inchiderea (+10 min de gratie)
              vazut_ultima=time.strftime("%H:%M:%S", time.localtime(e["last"])),
              text=t("{vehicul} {plecat} dupa ~{n} min (pierdut din vedere)", vehicul=t(_forma(e['lbl'])[0]),
                     plecat=t(_forma(e['lbl'])[1]), n=durata))


def suprapune(cam, box, refresh=False):
    """Centrul box-ului cade intr-o parcare cunoscuta (largita 20%)? -> eticheta ei.
    Folosit de detectie/confirmare ca sa nu re-raporteze obiectul parcat.

    refresh=True: potrivirea e si DOVADA ca obiectul e tot acolo — ii improspatam
    `last`. Fara asta, prezenta depindea doar de track-ul din urmarire: la prima
    parcare reala (2026-09-10, 11:18) track-ul a murit pe masina nemiscata,
    registrul a expirat "pierdut din vedere" si s-a re-deschis dupa un minut —
    desi detectia O VEDEA continuu (suprimare activa, 0 evenimente)."""
    cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
    with _lock:
        _load()
        for e in _reg.get(cam, {}).values():
            ex, ey, ew, eh = e["box"]
            mx, my = ew * 0.2, eh * 0.2
            if ex - mx <= cx <= ex + ew + mx and ey - my <= cy <= ey + eh + my:
                if refresh:
                    e["last"] = time.time()
                return e["lbl"]
    return None


def snapshot():
    """{cam: [{lbl, box, minute}]} — pentru overlay si /detect/state."""
    now = time.time()
    with _lock:
        _load()
        return {cam: [{"lbl": e["lbl"], "box": e["box"],
                       "minute": round((now - e["t0"]) / 60)}
                      for e in entries.values()]
                for cam, entries in _reg.items() if entries}
