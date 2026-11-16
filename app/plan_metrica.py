#!/usr/bin/env python3
"""Masura planului hartii: cum devin pixelii imaginii metri pe teren.

Sursa preferata e LIMITA PROPRIETATII: colturile ei (fractii din imagine) si lungimile reale
ale laturilor pe care le stii. Sursa de rezerva e SCARA din doua clicuri (o distanta).

Modelul: intre doua puncte ale planului aflate la (dx, dy) pixeli, distanta reala e
    d^2 = a*dx^2 + 2*b*dx*dy + c*dy^2        (G = [[a, b], [b, c]], in m^2/px^2)
- IZOTROP (G = s^2 * I): aceeasi scara pe toate directiile — o captura din satelit, un plan
  desenat la scara. Din 1–2 laturi cunoscute se poate afla doar asta.
- ANIZOTROP (G complet): scara difera pe directii — o schita de mana intinsa pe lungime sau
  pe latime. Cere cel putin 3 laturi pe directii diferite.
Modelul anizotrop se alege doar cand datele il cer limpede (vezi _alege): cu masuratori
aproximative (±2–5%), zgomotul nu trebuie confundat cu o deformare a planului.

A = radacina simetrica a lui G duce un vector de pixeli in metri (A^T A = G); unghiurile,
perimetrul si suprafata se calculeaza dupa A, deci sunt corecte si pe o schita deformata.
Totul e pur (fara Flask/fisiere), ca sa se poata testa si refolosi (zone, distante, viteze).
"""
import math

import numpy as np

from i18n import t

MIN_COLTURI, MAX_COLTURI = 3, 40
# pragurile alegerii modelului (abaterea relativa RMS a laturilor fata de lungimile introduse)
PRAG_ZGOMOT = 0.03        # sub atat, abaterile sunt masuratori aproximative, nu deformare
PRAG_EXACT_3 = 0.05       # cu exact 3 laturi modelul anizotrop se potriveste perfect: cerem mai mult
RAPORT_MAX = 3.0          # o deformare mai mare de 3:1 e mai probabil o lungime gresita
PRAG_AVERTISMENT = 0.10   # o latura cu peste 10% abatere merita aratata


def _sqrt_sim(g):
    """Radacina simetrica pozitiv definita a unei matrice 2x2 simetrice PD."""
    w, v = np.linalg.eigh(g)
    return v @ np.diag(np.sqrt(w)) @ v.T


def _pd(g):
    return g[0, 0] > 0 and g[1, 1] > 0 and np.linalg.det(g) > 0


def _abateri(g, laturi):
    """Abaterea relativa a fiecarei laturi cunoscute: lungime pe plan / lungime introdusa - 1."""
    return [math.sqrt(max(0.0, d @ g @ d)) / L - 1 for d, L in laturi]


def _rms(v):
    return math.sqrt(sum(x * x for x in v) / len(v)) if v else 0.0


def _izotrop(laturi):
    """s care minimizeaza sum((s*|d|/L - 1)^2) — abateri relative, nu absolute."""
    r = [float(np.hypot(*d)) / L for d, L in laturi]
    s = sum(r) / sum(x * x for x in r)
    return np.eye(2) * s * s


def _anizotrop(laturi):
    """G din cele mai mici patrate pe ecuatiile d^T G d / L^2 = 1, sau None daca datele nu-l
    determina (directii prea putin diferite) ori iese nefizic (nu e pozitiv definit)."""
    m = np.array([[d[0] ** 2, 2 * d[0] * d[1], d[1] ** 2] for d, L in laturi]) / \
        np.array([[L * L] for d, L in laturi])
    sv = np.linalg.svd(m, compute_uv=False)
    if len(laturi) < 3 or sv[-1] / sv[0] < 1e-3:
        return None
    a, b, c = np.linalg.lstsq(m, np.ones(len(laturi)), rcond=None)[0]
    g = np.array([[a, b], [b, c]])
    return g if _pd(g) else None


def _alege(laturi):
    """(G, mod, avertismente): izotrop implicit; anizotrop doar cand se potriveste limpede mai bine."""
    g_iso = _izotrop(laturi)
    rms_iso = _rms(_abateri(g_iso, laturi))
    avert = []
    g_ani = _anizotrop(laturi) if len(laturi) >= 3 else None
    if g_ani is not None:
        w = np.linalg.eigvalsh(g_ani)
        raport = math.sqrt(w[1] / w[0])
        rms_ani = _rms(_abateri(g_ani, laturi))
        prag = PRAG_EXACT_3 if len(laturi) == 3 else PRAG_ZGOMOT
        if raport > RAPORT_MAX:
            avert.append(t("laturile cer o deformare a planului mai mare de 3:1 — probabil o lungime e greșită; folosesc o scară unică"))
        elif rms_iso > prag and (len(laturi) == 3 or rms_ani < 0.6 * rms_iso):
            return g_ani, "anizotrop", avert
    elif len(laturi) >= 3 and rms_iso > PRAG_ZGOMOT:
        avert.append(t("laturile nu se potrivesc între ele, iar o corecție pe direcții nu le explică; verifică lungimile"))
    return g_iso, "izotrop", avert


def _intersectie(p1, p2, p3, p4):
    """Segmentele [p1,p2] si [p3,p4] se taie (strict, nu doar la capete)?"""
    def o(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    d1, d2, d3, d4 = o(p3, p4, p1), o(p3, p4, p2), o(p1, p2, p3), o(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def valideaza_limita(v):
    """Normalizeaza limita din UI: {puncte: [[x,y]...] fractii, laturi: {i: metri}}."""
    try:
        pts = [(float(p[0]), float(p[1])) for p in v["puncte"]]
        laturi = {int(i): float(m) for i, m in (v.get("laturi") or {}).items()
                  if m is not None and str(m).strip() != ""}
    except (KeyError, TypeError, ValueError, IndexError):
        raise ValueError(t("limită invalidă"))
    n = len(pts)
    if not (MIN_COLTURI <= n <= MAX_COLTURI):
        raise ValueError(t("limita are între {a} și {b} colțuri", a=MIN_COLTURI, b=MAX_COLTURI))
    if not all(0 <= x <= 1 and 0 <= y <= 1 for x, y in pts):
        raise ValueError(t("colțurile limitei trebuie să fie pe plan"))
    for i in range(n):
        if math.hypot(pts[i][0] - pts[(i + 1) % n][0], pts[i][1] - pts[(i + 1) % n][1]) < 0.002:
            raise ValueError(t("două colțuri ale limitei sunt în același loc"))
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue                       # laturi vecine (prin colțul 0)
            if _intersectie(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n]):
                raise ValueError(t("limita se intersectează cu ea însăși"))
    for i, m in laturi.items():
        if not 0 <= i < n:
            raise ValueError(t("latură inexistentă: {i}", i=i + 1))
        if not 0.1 <= m <= 100000:
            raise ValueError(t("lungimea unei laturi trebuie să fie între 0,1 și 100000 m"))
    return {"puncte": [[round(x, 4), round(y, 4)] for x, y in pts],
            "laturi": {str(i): round(m, 2) for i, m in sorted(laturi.items())}}


def _unghiuri(q):
    """Unghiurile interioare (grade) ale poligonului q (in metri), in ordinea colturilor."""
    n = len(q)
    arie = sum(q[i][0] * q[(i + 1) % n][1] - q[(i + 1) % n][0] * q[i][1] for i in range(n)) / 2
    out = []
    for i in range(n):
        v1, v2 = q[i - 1] - q[i], q[(i + 1) % n] - q[i]
        u = math.degrees(math.atan2(v1[0] * v2[1] - v1[1] * v2[0], v1 @ v2)) % 360
        out.append(round(u if arie < 0 else 360 - u, 1))
    return out, abs(arie)


def calculeaza(w, h, limita=None, scara=None):
    """Masura planului (imagine w x h pixeli) din limita (preferata) sau din scara. None fara
    nicio lungime cunoscuta. Intoarce dictionarul folosit de interfata (vezi docstring-ul modulului)."""
    if not w or not h:
        return None
    pts = [np.array([p[0] * w, p[1] * h]) for p in (limita or {}).get("puncte") or []]
    n = len(pts)
    cunoscute = {int(i): float(m) for i, m in ((limita or {}).get("laturi") or {}).items()} if n else {}
    laturi = [(pts[(i + 1) % n] - pts[i], m) for i, m in sorted(cunoscute.items())]
    avert = []
    if laturi:
        g, mod, avert = _alege(laturi)
        sursa = "limita"
    elif scara:
        d = np.array([(scara["x2"] - scara["x1"]) * w, (scara["y2"] - scara["y1"]) * h])
        s = float(scara["m"]) / float(np.hypot(*d))
        g, mod, sursa = np.eye(2) * s * s, "izotrop", "scara"
    else:
        return None
    a = _sqrt_sim(g)
    out = {"G": [round(float(g[0, 0]), 10), round(float(g[0, 1]), 10), round(float(g[1, 1]), 10)],
           "A": [[float(x) for x in r] for r in a], "Ainv": [[float(x) for x in r] for r in np.linalg.inv(a)],
           "mod": mod, "sursa": sursa, "w": int(w), "h": int(h), "avertismente": avert}
    ev = np.linalg.eigh(g)
    if mod == "anizotrop":
        lung = ev[1][:, 1]                        # directia cu mai multi metri pe pixel
        out["deformare"] = {"raport": round(math.sqrt(ev[0][1] / ev[0][0]), 3),
                            "unghi": round(math.degrees(math.atan2(lung[1], lung[0])) % 180, 1)}
    if n:
        q = [a @ p for p in pts]
        out["laturi"] = []
        for i in range(n):
            m_plan = float(np.hypot(*(q[(i + 1) % n] - q[i])))
            m_in = cunoscute.get(i)
            out["laturi"].append({"m_plan": round(m_plan, 2), "m_introdus": m_in,
                                  "abatere": round(m_plan / m_in - 1, 4) if m_in else None})
        out["unghiuri"], arie = _unghiuri(q)
        out["perimetru"] = round(sum(x["m_plan"] for x in out["laturi"]), 1)
        out["suprafata"] = round(arie, 1)
        ab = [abs(x["abatere"]) for x in out["laturi"] if x["abatere"] is not None]
        out["abatere_max"] = round(max(ab), 4) if ab else None
        if ab and max(ab) > PRAG_AVERTISMENT:
            out["avertismente"].append(t("o latură diferă cu peste 10% de lungimea introdusă — verifică lungimea sau colțurile"))
    return out
