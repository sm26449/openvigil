#!/usr/bin/env python3
"""Reguli de analiză: stratul subțire „ce înseamnă și pe cine anunț" peste percepție
(YOLO / track / scenă / VLM). Vezi docs/reguli-analiza.md.

Aici stă DOAR logica pură, testabilă fără model: scorul geometric de cădere din
keypoints + mașina de stări care cere persistență (dwell) + cooldown-ul de alertă.
Partea cu model (pose în nvr-ai) și cârligul din detect.py vin în faza 2; confirmarea
VLM și escaladarea (nvr-web) în faza 3 — toate apelează funcțiile de aici.

Keypoints: formatul COCO-17 (ultralytics pose), listă de (x, y, incredere) în pixeli.
Geometria e relativă (unghiuri, rapoarte), deci e invariantă la scară.
"""
import math

# indici COCO-17
NAS, OCHI_S, OCHI_D, URECHE_S, URECHE_D = 0, 1, 2, 3, 4
UMAR_S, UMAR_D = 5, 6
SOLD_S, SOLD_D = 11, 12


def _pct(kps, i, conf_min):
    """(x, y) pentru keypoint-ul i dacă e destul de sigur, altfel None."""
    if i >= len(kps):
        return None
    x, y, c = kps[i]
    return (float(x), float(y)) if c >= conf_min else None


def _medie(puncte):
    """Media punctelor non-None, sau None dacă niciunul nu e valid."""
    bune = [p for p in puncte if p is not None]
    if not bune:
        return None
    return (sum(p[0] for p in bune) / len(bune), sum(p[1] for p in bune) / len(bune))


def _clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def scor_cadere(keypoints, bbox, conf_min=0.5):
    """Scor în [0, 1]: cât de „întinsă/orizontală" e persoana. Combină trei semnale,
    fiecare folosit doar dacă keypoints-urile lui sunt valide, altfel cade pe bbox.

    - trunchi (0.5): linia umeri→șolduri aproape de orizontală;
    - raport bbox (0.3): lățime > înălțime;
    - cap (0.2): capul coborât spre nivelul șoldurilor.
    """
    x1, y1, x2, y2 = (float(v) for v in bbox)
    w, h = max(1.0, x2 - x1), max(1.0, y2 - y1)

    umeri = _medie([_pct(keypoints, UMAR_S, conf_min), _pct(keypoints, UMAR_D, conf_min)])
    solduri = _medie([_pct(keypoints, SOLD_S, conf_min), _pct(keypoints, SOLD_D, conf_min)])
    cap = _medie([_pct(keypoints, NAS, conf_min), _pct(keypoints, OCHI_S, conf_min),
                  _pct(keypoints, OCHI_D, conf_min)])

    componente = []  # (greutate, valoare)

    # raport bbox: 0 sub 0.8 (vertical), 1 peste 1.6 (lat/întins)
    componente.append((0.3, _clamp((w / h - 0.8) / 0.8)))

    if umeri and solduri:
        dx, dy = solduri[0] - umeri[0], solduri[1] - umeri[1]
        lung = math.hypot(dx, dy)
        if lung > 1e-6:
            # orizontalitatea trunchiului: 0 = vertical (în picioare), 1 = culcat
            componente.append((0.5, _clamp(abs(dx) / lung)))
        if cap:
            # poziția capului între umeri (0) și șolduri (1): în picioare capul e deasupra
            # umerilor (→0), culcat ajunge la nivelul șoldurilor (→1)
            interval = solduri[1] - umeri[1]
            if abs(interval) > 1e-6:
                componente.append((0.2, _clamp((cap[1] - umeri[1]) / interval)))

    gtot = sum(g for g, _ in componente)
    return sum(g * v for g, v in componente) / gtot if gtot else 0.0


class Cadere:
    """Mașina de stări a persistenței: o persoană trebuie să stea jos (scor ≥ prag)
    neîntrerupt ≥ `dwell_s` secunde ca să conteze drept candidat de cădere. Se
    anulează dacă se ridică (scor sub prag). Emite candidatul O SINGURĂ dată per
    episod — confirmarea VLM și cooldown-ul de alertă sunt separate (ale apelantului).

    Stare per (cameră, track_id); `now` injectat, deci pur testabil.
    """

    def __init__(self, dwell_s=6.0, scor_prag=0.6):
        self.dwell_s = float(dwell_s)
        self.scor_prag = float(scor_prag)
        self._jos = {}       # (cam, track) -> {"t0", "emis", "last"}

    def update(self, cam, track_id, scor, now):
        """Întoarce True exact o dată, când dwell-ul s-a împlinit. Altfel False."""
        key = (int(cam), track_id)
        if scor < self.scor_prag:
            self._jos.pop(key, None)
            return False
        st = self._jos.get(key)
        if st is None:
            self._jos[key] = {"t0": now, "emis": False, "last": now}
            return False
        st["last"] = now
        if not st["emis"] and now - st["t0"] >= self.dwell_s:
            st["emis"] = True
            return True
        return False

    def expire(self, now, max_age=30.0):
        """Uită track-urile care n-au mai fost actualizate de `max_age` secunde
        (au ieșit din cadru) — altfel un episod vechi ar bloca unul nou pe același id."""
        for key in [k for k, st in self._jos.items() if now - st["last"] > max_age]:
            self._jos.pop(key, None)


class Cooldown:
    """Rată per cheie (ex. cameră): `gata(cheie, now)` întoarce True și marchează
    momentul doar dacă au trecut cel puțin `sec` secunde de la ultima dată."""

    def __init__(self, sec):
        self.sec = float(sec)
        self._ultim = {}

    def gata(self, cheie, now):
        if now - self._ultim.get(cheie, float("-inf")) < self.sec:
            return False
        self._ultim[cheie] = now
        return True
