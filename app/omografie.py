#!/usr/bin/env python3
"""Omografia camera -> plan: din perechi de puncte (imaginea camerei <-> planul hartii)
o matrice 3x3 care duce orice punct DE PE SOL din cadru pe plan.

Toate coordonatele sunt fractii (0..1): in cadru (x spre dreapta, y in jos) si pe imaginea
planului. Valabila doar pentru puncte de pe sol (picioarele unui om, rotile unei masini) —
de aceea o detectie se proiecteaza prin mijlocul laturii de jos a chenarului.

Calculul: DLT normalizat (Hartley) prin SVD, cu 4..MAX_PERECHI perechi; la mai mult de 4,
cele mai mici patrate. `eroare` = cea mai mare abatere a perechilor reproiectate, in
fractii din plan (interfata o arata in metri cand planul are scara).
"""
from itertools import combinations

import numpy as np

from i18n import t

MIN_PERECHI = 4
MAX_PERECHI = 12


def _norm(p):
    """Translatie + scalare care aduc punctele in jurul originii, la distanta medie √2."""
    c = p.mean(axis=0)
    d = np.sqrt(((p - c) ** 2).sum(axis=1)).mean()
    s = np.sqrt(2) / d if d > 1e-9 else 1.0
    return np.array([[s, 0, -s * c[0]], [0, s, -s * c[1]], [0, 0, 1]])


def _rasfirate(p, prag):
    """Exista 4 puncte din care niciun trio nu e (aproape) pe o linie? Fara ele, o
    omografie nu e determinata — orice alte puncte doar o netezesc."""
    arie = lambda a, b, c: abs((b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])) / 2
    return any(all(arie(*tr) >= prag for tr in combinations(q, 3)) for q in combinations(p, 4))


def proiecteaza(h, x, y):
    """Punctul (x, y) din cadru pe plan, sau None daca e dincolo de orizont."""
    m = np.asarray(h, dtype=float).reshape(3, 3)
    v = m @ np.array([x, y, 1.0])
    if v[2] <= 1e-9:
        return None
    return float(v[0] / v[2]), float(v[1] / v[2])


def calculeaza(perechi):
    """perechi = [[cx, cy, px, py], ...] -> {"perechi", "h": [9], "eroare"}; ValueError
    pe date proaste (prea putine/multe, in afara cadrului, coliniare, degenerate)."""
    try:
        pp = np.array([[float(v) for v in p] for p in perechi], dtype=float)
    except (TypeError, ValueError):
        raise ValueError(t("perechi de puncte invalide"))
    if pp.ndim != 2 or pp.shape[1] != 4:
        raise ValueError(t("perechi de puncte invalide"))
    if not (MIN_PERECHI <= len(pp) <= MAX_PERECHI):
        raise ValueError(t("calibrarea cere între {a} și {b} perechi de puncte",
                           a=MIN_PERECHI, b=MAX_PERECHI))
    if not np.all(np.isfinite(pp)) or pp.min() < 0 or pp.max() > 1:
        raise ValueError(t("punctele trebuie să fie în imagine (fracții 0..1)"))
    cam, plan = pp[:, :2], pp[:, 2:]
    # 4 puncte din care 3 pe o linie nu fixeaza o omografie
    if not _rasfirate(cam, 2e-3) or not _rasfirate(plan, 2e-4):
        raise ValueError(t("punctele sunt prea apropiate sau pe aceeași linie — alege-le răsfirate"))
    tc, tp = _norm(cam), _norm(plan)
    ca = (tc @ np.c_[cam, np.ones(len(cam))].T).T
    pa = (tp @ np.c_[plan, np.ones(len(plan))].T).T
    rows = []
    for (x, y, _), (u, v, _) in zip(ca, pa):
        rows.append([-x, -y, -1, 0, 0, 0, u * x, u * y, u])
        rows.append([0, 0, 0, -x, -y, -1, v * x, v * y, v])
    _, s, vt = np.linalg.svd(np.array(rows))
    hn = vt[-1].reshape(3, 3)
    h = np.linalg.inv(tp) @ hn @ tc
    if abs(h[2, 2]) < 1e-12 or abs(np.linalg.det(h)) < 1e-12:
        raise ValueError(t("punctele nu dau o transformare validă — verifică perechile"))
    h = h / h[2, 2]
    # toate punctele de calibrare trebuie sa cada in fata camerei (acelasi semn al lui w)
    err = 0.0
    for cx, cy, px, py in pp:
        r = proiecteaza(h, cx, cy)
        if r is None:
            raise ValueError(t("punctele nu dau o transformare validă — verifică perechile"))
        err = max(err, float(np.hypot(r[0] - px, r[1] - py)))
    return {"perechi": [[round(float(v), 4) for v in p] for p in pp],
            "h": [round(float(v), 8) for v in h.ravel()],
            "eroare": round(err, 4)}
