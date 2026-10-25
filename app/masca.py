#!/usr/bin/env python3
"""Masca de confidentialitate: dreptunghiuri (normalizate 0..1) innegrite pe o camera.

Utila cand o camera prinde si proprietatea vecinului sau strada: zona se acopera in tot ce
IESE din sistem — fluxul live din grila si HD, redarea, clipurile, exporturile si fisa
probatorie — printr-un filtru ffmpeg `drawbox ... t=fill`. Inregistrarea pe disc ramane
intreaga (masca e la vizionare, nu la stocare), ca o ancheta sa aiba materialul brut.

Dreptunghiurile stau in cameras.json, pe camera (`mask`). Fluxurile go2rtc le primesc prin
template-uri per camera generate in gen_go2rtc.py (`m<id>`, `m<id>s2`...), pentru ca
go2rtc refuza sursele cu spatii (#raw=... nu merge), iar template-urile pot avea spatii.
Limitare: o camera cu masca se reda la rezolutie nativa (treapta 720/1080 nu se aplica —
scalarea ar fi un al doilea -vf). Pozele de detectie pe HD (cadrul confirmat) raman
nemascate: sunt interne, nu ies din sistem.
"""
MAX_RECTS = 8


def normalizeaza(rects):
    """Lista de dreptunghiuri [x, y, w, h] (fractii), curatata; None/lista goala = fara masca."""
    if not rects:
        return None
    out = []
    for r in rects[:MAX_RECTS]:
        try:
            x, y, w, h = (float(v) for v in (r if isinstance(r, (list, tuple)) else
                                              (r["x"], r["y"], r["w"], r["h"])))
        except (KeyError, TypeError, ValueError):
            raise ValueError("dreptunghi invalid: [x, y, w, h] între 0 și 1")
        x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
        w, h = max(0.0, min(1.0 - x, w)), max(0.0, min(1.0 - y, h))
        if w < 0.005 or h < 0.005:
            continue
        out.append([round(x, 4), round(y, 4), round(w, 4), round(h, 4)])
    return out or None


def vf(rects):
    """Lantul de filtre ffmpeg care innegreste dreptunghiurile (sau sir gol)."""
    return ",".join(f"drawbox=x=iw*{x}:y=ih*{y}:w=iw*{w}:h=ih*{h}:color=black:t=fill"
                    for x, y, w, h in (rects or []))


def rects(cam):
    """Masca unei intrari de camera (dict din cameras.json / get_cameras) sau None."""
    return normalizeaza((cam or {}).get("mask"))


def vf_cam(cam):
    return vf(rects(cam))
