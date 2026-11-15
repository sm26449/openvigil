#!/usr/bin/env python3
"""Analitica retroactiva din ce avem deja in jurnal si pe disc (Etapa 5).

  - cautare pe REGIUNE: evenimentele unei zile ale caror chenare (`chenar` = [x,y,w,h]
    normalizat, atasat de detectie/urmarire la emitere) ating un dreptunghi desenat pe
    cadru — „cine a trecut pe la poarta mica?" fara sa derulezi ziua;
  - HARTA DE CALDURA: traseele oamenilor (`traseu` din episoade) adunate pe o grila
    64x36 per camera si interval — pe unde se umbla cu adevarat; pe PLANUL hartii (picioarele,
    proiectate prin calibrarea fiecarei camere) cu heatmap_plan;
  - TIME-LAPSE: ziua unei camere din cate un cadru pe minut, extras prin seek din fiecare
    segment (nu se citeste tot materialul), 24 h -> ~1 minut de video, ca job in coada.
"""
import os
import time

import jurnal
import record
import logare
from i18n import t

GRID_W, GRID_H = 64, 36
MAX_EPISOADE = 20000   # pe camera si cerere; peste, rezultatul e marcat `partial` (audit F7)
TIPURI_CHENAR = ("detectie", "intrus", "obiect")


def _sec(t):
    """'YYYY-MM-DD HH:MM:SS' -> secunda zilei."""
    try:
        return int(t[11:13]) * 3600 + int(t[14:16]) * 60 + int(t[17:19])
    except (TypeError, ValueError):
        return 0


def se_ating(a, b):
    """Doua dreptunghiuri [x, y, w, h] se intersecteaza? (atingerea pe margine nu conteaza)"""
    return not (a[0] + a[2] <= b[0] or b[0] + b[2] <= a[0]
                or a[1] + a[3] <= b[1] or b[1] + b[3] <= a[1])


def regiune(cam, day, rect, limit=300):
    """Evenimentele zilei cu chenar in dreptunghiul dat, cele mai noi primele."""
    x, y, w, h = rect
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1):
        raise ValueError(t("regiune invalida"))
    out = []
    for e in jurnal.query(limit=20000, cam=cam, day=day):
        ch = e.get("chenar")
        if e.get("tip") not in TIPURI_CHENAR or not isinstance(ch, list) or len(ch) != 4:
            continue
        if se_ating(ch, [x, y, w, h]):
            out.append({"id": e.get("id"), "t": e.get("t"), "sec": _sec(e.get("t")),
                        "tip": e.get("tip"), "eticheta": e.get("eticheta") or e.get("eticheta_ro"),
                        "chenar": ch})
            if len(out) >= limit:
                break
    return out


def _interval(day_end, zile):
    zile = max(1, min(90, int(zile)))
    end_day = day_end or time.strftime("%Y-%m-%d")
    t_end = time.mktime(time.strptime(end_day, "%Y-%m-%d")) + 86400
    return zile, end_day, t_end - zile * 86400, t_end


def heatmap(cam, day_end=None, zile=1):
    """Grila GRID_W x GRID_H cu numarul de puncte de traseu (oameni) pe celula, pentru
    camera `cam` in ultimele `zile` zile pana la `day_end` (implicit azi)."""
    zile, end_day, t_start, t_end = _interval(day_end, zile)
    grid = [[0] * GRID_W for _ in range(GRID_H)]
    episoade = 0
    evs = jurnal.query(limit=MAX_EPISOADE, cam=cam, tip="episod", since=t_start, until=t_end)
    for e in evs:
        tr = e.get("traseu")
        if not isinstance(tr, list):
            continue
        episoade += 1
        for p in tr:
            try:
                gx = min(GRID_W - 1, max(0, int(float(p[0]) * GRID_W)))
                gy = min(GRID_H - 1, max(0, int(float(p[1]) * GRID_H)))
            except (TypeError, ValueError, IndexError):
                continue
            grid[gy][gx] += 1
    return {"w": GRID_W, "h": GRID_H, "grid": grid, "max": max(max(r) for r in grid),
            "episoade": episoade, "zile": zile, "pana_la": end_day,
            "partial": len(evs) >= MAX_EPISOADE}


PLAN_GRID = 128   # celule pe latura planului (fractii), pentru harta de caldura pe plan


def heatmap_plan(pid, day_end=None, zile=1, visible=None):
    """Harta de caldura pe PLAN: picioarele oamenilor (traseele episoadelor) ale camerelor
    calibrate, proiectate prin omografia fiecareia si adunate pe o grila PLAN_GRID^2 din
    fractiile planului. Punctele vechi, fara jumatatea inaltimii chenarului (dinainte ca
    traseul s-o retina), nu au punct de sol: le numaram separat, nu le ghicim."""
    import harta
    import omografie
    zile, end_day, t_start, t_end = _interval(day_end, zile)
    plan = next((p for p in harta.lista(visible) if p["id"] == pid), None)
    if plan is None:
        raise ValueError(t("planul nu există"))
    celule, episoade, fara_sol, camere, partial = {}, 0, 0, [], False
    for cid, poz in plan["camere"].items():
        cal = poz.get("calibrare")
        if not cal:
            continue
        camere.append(int(cid))
        evs = jurnal.query(limit=MAX_EPISOADE, cam=int(cid), tip="episod", since=t_start, until=t_end)
        partial = partial or len(evs) >= MAX_EPISOADE   # cele mai vechi episoade au ramas pe dinafara
        for e in evs:
            tr = e.get("traseu")
            if not isinstance(tr, list):
                continue
            episoade += 1
            for p in tr:
                try:
                    if len(p) < 3:
                        fara_sol += 1
                        continue
                    r = omografie.proiecteaza(cal["h"], float(p[0]), float(p[1]) + float(p[2]))
                except (TypeError, ValueError, IndexError):
                    continue
                if r is None or not (0 <= r[0] < 1 and 0 <= r[1] < 1):
                    continue
                k = (int(r[0] * PLAN_GRID), int(r[1] * PLAN_GRID))
                celule[k] = celule.get(k, 0) + 1
    return {"grid": PLAN_GRID, "celule": [[x, y, n] for (x, y), n in celule.items()],
            "max": max(celule.values(), default=0), "puncte": sum(celule.values()),
            "episoade": episoade, "fara_sol": fara_sol, "camere": sorted(camere),
            "zile": zile, "pana_la": end_day, "partial": partial}


# ---- time-lapse ----
def segmente_zi(cam, day):
    """Fisierele unei zile locale: [(epoch, cale)], in ordine."""
    local_day = lambda ts: time.strftime("%Y-%m-%d", time.localtime(ts))
    return [(t, p) for t, p, _ in record._files(cam) if local_day(t) == day]


def cadre(seg, pas=60):
    """Momentele de extras: [(cale, decalaj_sec)] — cate un cadru la `pas` secunde, aliniat
    la ceasul zilei, cautat prin seek IN fisier (nu citim tot materialul: o zi de HEVC la
    6 MP inseamna zeci de GB, iar `-skip_frame nokey` tot le citeste de pe disc)."""
    out = []
    for t, cale in seg:
        zi0 = t - (time.localtime(t).tm_hour * 3600 + time.localtime(t).tm_min * 60
                   + time.localtime(t).tm_sec)
        k = int((t - zi0 + pas - 1) // pas)          # primul moment aliniat >= t
        while zi0 + k * pas < t + record.SEGMENT:
            out.append((cale, round(zi0 + k * pas - t, 1)))
            k += 1
    return out


def comanda_cadru(cale, decalaj, out_jpg, inaltime=720):
    """Un singur cadru (cel mai apropiat cadru-cheie dupa seek), scalat, ca JPEG."""
    vf = f"scale=-2:{int(inaltime)}" if inaltime else "null"
    return ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-ss", str(decalaj), "-i", cale,
            "-frames:v", "1", "-vf", vf, "-q:v", "4", out_jpg]


def comanda_timelapse(cadre_dir, out, hw=True, masca_vf=""):
    """Encodarea secventei de JPEG-uri (cadre/%05d.jpg) intr-un MP4 la 25 fps."""
    enc = (["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"] if hw
           else ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23"])
    vf = ["-vf", masca_vf] if masca_vf else []
    return (["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-framerate", "25",
             "-i", os.path.join(cadre_dir, "%05d.jpg")] + vf
            + ["-an", "-pix_fmt", "yuv420p"] + enc + ["-movflags", "+faststart", out])


def job_timelapse(job, run_ffmpeg, hw, masca_vf="", lucratori=4, run=None):
    """Handler de job: {channel, day, pas}. Extrage cadrele in paralel (seek-uri scurte,
    ~0.1 s fiecare), apoi le encodeaza. `run_ffmpeg(cmd, durata, timeout, job)` = _run_ffmpeg
    din server (progres + anulare) pentru encodare; `run` = subprocess.run (inlocuibil in teste)."""
    import shutil
    import subprocess
    from concurrent.futures import ThreadPoolExecutor
    run = run or subprocess.run
    p = job.parametri
    cam, day, pas = int(p["channel"]), str(p["day"]), int(p.get("pas") or 60)
    seg = segmente_zi(cam, day)
    if not seg:
        raise RuntimeError(t("nu există înregistrări în ziua aleasă"))
    momente = cadre(seg, pas)
    job.progres(1, t("{segmente} segmente, {cadre} cadre", segmente=len(seg), cadre=len(momente)))
    cadre_dir = job.cale("cadre")
    os.makedirs(cadre_dir, exist_ok=True)
    gata = [0]

    def unul(i_m):
        i, (cale, dec) = i_m
        if job.anulat:
            return
        try:
            run(comanda_cadru(cale, dec, os.path.join(cadre_dir, f"{i:05d}.jpg")),
                capture_output=True, timeout=30)
        except Exception as e:
            logare.info(f"[timelapse] cadru {i}: {str(e)[:60]}")
        gata[0] += 1
        if gata[0] % 20 == 0:
            job.progres(1 + int(79 * gata[0] / max(1, len(momente))), t("cadre: {gata}/{total}", gata=gata[0], total=len(momente)))
    try:
        with ThreadPoolExecutor(max_workers=lucratori) as ex:
            list(ex.map(unul, enumerate(momente)))
        if job.anulat:
            raise RuntimeError(t("anulat"))
        # numerotare continua: un cadru lipsa (fisier corupt) nu rupe secventa
        jpgs = sorted(f for f in os.listdir(cadre_dir) if f.endswith(".jpg"))
        if not jpgs:
            raise RuntimeError(t("niciun cadru extras"))
        for i, f in enumerate(jpgs):
            if f != f"{i:05d}.jpg":
                os.replace(os.path.join(cadre_dir, f), os.path.join(cadre_dir, f"{i:05d}.jpg"))
        job.progres(82, t("encodez {n} cadre", n=len(jpgs)))
        out = job.cale("mp4")
        rc, err, expirat = run_ffmpeg(comanda_timelapse(cadre_dir, out, hw, masca_vf),
                                      durata=len(jpgs) / 25, timeout=600, job=job)
        if expirat:
            raise RuntimeError(t("time-lapse: timeout la encodare"))
        if rc != 0 or not os.path.exists(out) or os.path.getsize(out) < 1000:
            raise RuntimeError(t("time-lapse eșuat: {err}", err=(err or t("fișier gol"))[-200:]))
    finally:
        shutil.rmtree(cadre_dir, ignore_errors=True)
    job.fisier(out, f"cam{cam}_{day}_timelapse.mp4", "video/mp4")
    return {"octeti": os.path.getsize(out), "segmente": len(seg), "cadre": len(jpgs), "pas": pas}
