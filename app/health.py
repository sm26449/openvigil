#!/usr/bin/env python3
"""Verificari de sanatate: fluxul video al unei camere si starea serverului.

Camera poate raspunde perfect la interogari si totusi sa nu livreze imagine (s-a intamplat).
De aceea, la adaugare verificam efectiv fluxul RTSP, nu doar ca raspunde ISAPI.
"""
import json
import os
import subprocess
import time
from i18n import t
import urllib.parse

import logare


def check_stream(cam, channel=102, timeout=12):
    """Se conecteaza la fluxul RTSP si citeste primul cadru. Intoarce ce a gasit."""
    import drivers
    try:
        url = drivers.rtsp(cam, channel)      # main/sub dupa producator (Hikvision/ONVIF)
    except Exception as e:
        return {"ok": False, "secunde": 0, "eroare": logare.fara_parole(str(e))[:120]}
    try:
        t0 = time.time()
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-rtsp_transport", "tcp",
             "-select_streams", "v:0", "-show_entries",
             "stream=codec_name,width,height,avg_frame_rate",
             "-of", "json", "-timeout", str(timeout * 1000000), url],
            capture_output=True, timeout=timeout + 5)
        dt = round(time.time() - t0, 1)
        if r.returncode != 0:
            err = r.stderr.decode(errors="ignore").strip().splitlines()
            return {"ok": False, "secunde": dt,
                    "eroare": logare.fara_parole(err[-1] if err else t("flux indisponibil"))[:120]}
        st = (json.loads(r.stdout or "{}").get("streams") or [{}])[0]
        fr = st.get("avg_frame_rate", "0/1")
        try:
            n, d = fr.split("/")
            fps = round(int(n) / int(d)) if int(d) else 0
        except Exception:
            fps = 0
        return {"ok": True, "secunde": dt, "codec": st.get("codec_name"),
                "latime": st.get("width"), "inaltime": st.get("height"), "fps": fps}
    except subprocess.TimeoutExpired:
        return {"ok": False, "eroare": t("camera nu a trimis imagine in timpul alocat")}
    except Exception as e:
        return {"ok": False, "eroare": logare.fara_parole(str(e))[:120]}


# ---- starea serverului ----
_net_prev = {"t": 0, "rx": 0, "tx": 0}


def _cpu_percent():
    def snap():
        with open("/proc/stat") as f:
            p = f.readline().split()[1:8]
        v = [int(x) for x in p]
        return sum(v), v[3]          # total, idle
    t1, i1 = snap()
    time.sleep(0.15)
    t2, i2 = snap()
    dt, di = t2 - t1, i2 - i1
    return round(100 * (1 - di / dt), 1) if dt else 0


def system_status(rec_dir):
    """Procesor, memorie, disc si trafic de retea — cifrele care spun cand ne apropiem
    de limite."""
    import shutil
    with open("/proc/meminfo") as f:
        mem = {}
        for line in f:
            k, v = line.split(":", 1)
            mem[k] = int(v.strip().split()[0]) * 1024
    total = mem.get("MemTotal", 0)
    avail = mem.get("MemAvailable", 0)

    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in f.readlines()[2:]:
            name, rest = line.split(":", 1)
            if name.strip() in ("lo",):
                continue
            p = rest.split()
            rx += int(p[0])
            tx += int(p[8])
    now = time.time()
    speed = {"in_mbps": 0, "out_mbps": 0}
    if _net_prev["t"] and now > _net_prev["t"]:
        dt = now - _net_prev["t"]
        speed = {"in_mbps": round((rx - _net_prev["rx"]) * 8 / dt / 1e6, 1),
                 "out_mbps": round((tx - _net_prev["tx"]) * 8 / dt / 1e6, 1)}
    _net_prev.update(t=now, rx=rx, tx=tx)

    du = shutil.disk_usage(rec_dir if os.path.isdir(rec_dir) else "/")
    rec_size = 0
    try:
        for d in os.listdir(rec_dir):
            p = os.path.join(rec_dir, d)
            if os.path.isdir(p):
                rec_size += sum(os.path.getsize(os.path.join(p, f))
                                for f in os.listdir(p) if f.endswith(".mp4"))
    except OSError:
        pass
    return {
        "cpu": _cpu_percent(),
        "cores": os.cpu_count(),
        "load": [round(x, 2) for x in os.getloadavg()],
        "ram_used_gb": round((total - avail) / 2**30, 1),
        "ram_total_gb": round(total / 2**30, 1),
        "disk_free_gb": round(du.free / 2**30, 1),
        "disk_total_gb": round(du.total / 2**30, 1),
        "rec_gb": round(rec_size / 2**30, 1),
        "net": speed,
        "uptime_h": round(float(open("/proc/uptime").read().split()[0]) / 3600, 1),
    }
