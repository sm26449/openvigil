#!/usr/bin/env python3
"""Analitica peste urmarire (nvr-ai, Faza 2) — consuma track-uri, scoate intelesuri.

Urmarirea da pozitia fiecarui obiect cadru cu cadru. Aici scoatem din traiectorii
lucruri utile pentru un operator:
  - linii de numarare (tripwire): cate obiecte au TRECUT o linie si in ce sens
    (intra / iese) — "cate masini au intrat vs au iesit azi";
  - zone: cand un obiect INTRA intr-un poligon ("cineva a intrat in curte");
  - viteza: din deplasarea neta pe durata (px/s, sau km/h cu un factor de calibrare);
  - zabovire: obiect ramas mai mult de N secunde (loitering).

Configuratia (liniile/zonele per camera) vine din settings, scrisa din UI. Totul e
geometrie pura, testabila fara model/camera.
"""
import settings

DWELL_SEC = 30          # peste atat un obiect stationar devine "zabovire"


def lines(cam_id):
    """Liniile de numarare ale unei camere: [{name, p1:[x,y], p2:[x,y]}] normalizate."""
    return (settings.get("count_lines") or {}).get(str(cam_id)) or []


def zones(cam_id):
    """Zonele de intrare: [{name, poly:[[x,y],...]}] normalizate."""
    return (settings.get("count_zones") or {}).get(str(cam_id)) or []


def _ccw(a, b, c):
    return (c[1] - a[1]) * (b[0] - a[0]) > (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(p1, p2, p3, p4):
    """Segmentele p1p2 si p3p4 se intersecteaza?"""
    return (_ccw(p1, p3, p4) != _ccw(p2, p3, p4)
            and _ccw(p1, p2, p3) != _ccw(p1, p2, p4))


def _side(a, b, pt):
    """Semnul spune de ce parte a dreptei ab e punctul pt."""
    return (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0])


def crossings(cam_id, old_xy, new_xy, wh):
    """Liniile traversate de segmentul old->new: [(nume, 'intra'|'iese')].
    old_xy/new_xy in pixeli; liniile din config sunt normalizate [0,1]."""
    w, h = wh
    on = old_xy
    nn = new_xy
    out = []
    for ln in lines(cam_id):
        try:
            p1 = (ln["p1"][0] * w, ln["p1"][1] * h)
            p2 = (ln["p2"][0] * w, ln["p2"][1] * h)
        except (KeyError, TypeError, IndexError):
            continue
        if _segments_cross(on, nn, p1, p2):
            sens = "intra" if _side(p1, p2, nn) > 0 else "iese"
            out.append((ln.get("name", "linie"), sens))
    return out


def _point_in_poly(pt, poly):
    """Ray casting — punctul e in poligon?"""
    x, y = pt
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def zone_entries(cam_id, old_xy, new_xy, wh):
    """Zonele in care obiectul tocmai a INTRAT (era afara, acum inauntru): [nume]."""
    w, h = wh
    out = []
    for z in zones(cam_id):
        poly = [(p[0] * w, p[1] * h) for p in z.get("poly", []) if len(p) == 2]
        if len(poly) < 3:
            continue
        if _point_in_poly(new_xy, poly) and not _point_in_poly(old_xy, poly):
            out.append(z.get("name", "zona"))
    return out


def speed_px_s(track):
    """Viteza medie in px/s din deplasarea neta pe durata track-ului."""
    dt = track["last"] - track["t0"]
    if dt < 0.3:
        return 0.0
    dx = track["x"] - track["x0"]
    dy = track["y"] - track["y0"]
    return round((dx * dx + dy * dy) ** 0.5 / dt, 1)


BICI_INALTIMI_S = 2.2   # inaltimi de corp pe secunda: mersul ~0,8, alergarea ~1,8, bicicleta 2,4-4
BICI_DURATA_MIN = 1.0   # un track de sub o secunda n-are viteza de incredere


def inaltimi_pe_s(px_s, box_h_px):
    """Viteza relativa la marimea omului: px/s impartit la inaltimea cutiei in pixeli.
    Nu cere calibrare in metri — un om de 1,7 m e el insusi rigla. None fara cutie."""
    if not box_h_px or box_h_px < 8:
        return None
    return round(px_s / box_h_px, 2)


def este_biciclist(eticheta, inaltimi_s, durata):
    """Un „om” care se deplaseaza cu peste BICI_INALTIMI_S inaltimi/s, suficient timp, e pe
    bicicleta (sau trotineta) — fara model nou, doar geometrie."""
    return (eticheta == "person" and inaltimi_s is not None and inaltimi_s >= BICI_INALTIMI_S
            and (durata or 0) >= BICI_DURATA_MIN)


def kmh(cam_id, px_s):
    """px/s -> km/h daca exista un factor de calibrare per camera (metri/pixel)."""
    cal = (settings.get("speed_cal") or {}).get(str(cam_id))
    if not cal:
        return None
    return round(px_s * float(cal) * 3.6, 1)
