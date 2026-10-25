#!/usr/bin/env python3
"""Masurarea continua a zgomotului pe camerele cu microfon, cu reguli de alarma.

Un fir per camera: ffmpeg decodeaza DOAR audio (8 kHz mono = 16 KB/s, cost neglijabil),
iar noi calculam nivelul in dBFS o data pe secunda. Pastram un istoric de o ora si
comparam cu un prag adaptiv (mediana ultimelor minute), ca sa nu depinda de cat de
zgomotoasa e in general locatia.

Doua reguli, ambele utile in practica:
- ZGOMOT SUSTINUT: nivelul sta peste prag mai mult de N secunde (utilaj, alarma, latrat);
- VARF BRUSC: crestere mare fata de fondul obisnuit (geam spart, impuscatura, tipat).
"""
import collections
import json
import os
import statistics
import subprocess
import threading
import time
import urllib.parse

import noise_stats
import logare

import config as C

GO2RTC_RTSP = getattr(C, "GO2RTC_RTSP", "rtsp://127.0.0.1:8554")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_FILE = os.path.join(ROOT, "data", "cameras.json")

HISTORY_SEC = 3600      # o ora de istoric, la o valoare pe secunda
BASELINE_SEC = 300      # fondul se calculeaza pe ultimele 5 minute
COOLDOWN = 60           # secunde intre doua alarme de zgomot pe aceeasi camera

monitors = {}


class AudioMonitor(threading.Thread):
    def __init__(self, cam, on_event=None):
        super().__init__(daemon=True, name=f"audio-cam{cam['id']}")
        self.cam = cam
        self.cam_id = cam["id"]
        self.stop_flag = False
        self.history = collections.deque(maxlen=HISTORY_SEC)   # (epoch, dbfs)
        self.level = None          # ultimul nivel masurat
        self.baseline = None       # fondul obisnuit al locului
        self.on_event = on_event
        self._loud_since = None
        self._last_alarm = 0
        self.error = None

    def _urls(self):
        """Substream-ul prin go2rtc (parola ramane in go2rtc.yaml, nu in `ps`), apoi direct
        de pe camera ca rezerva — se alterneaza la fiecare esec."""
        pw = urllib.parse.quote(self.cam["pass"], safe="")
        return [f"{GO2RTC_RTSP}/cam{self.cam['id']}",
                (f"rtsp://{self.cam['user']}:{pw}@{self.cam['ip']}:"
                 f"{self.cam.get('port', 554)}/Streaming/Channels/102")]

    def run(self):
        import numpy as np
        urls, attempt = self._urls(), 0
        while not self.stop_flag:
            p = None
            idx = attempt % len(urls)
            url = urls[idx]
            attempt += 1
            started = time.time()
            revenire = False
            try:
                p = subprocess.Popen(
                    ["ffmpeg", "-nostdin", "-loglevel", "error", "-rtsp_transport", "tcp",
                     # fara timeout de socket, un flux care amuteste fara sa inchida conexiunea
                     # ar tine read()-ul de mai jos blocat la nesfarsit (aceeasi lectie ca la
                     # record.py); cu el, ffmpeg iese si se trece la sursa urmatoare
                     "-timeout", "20000000",
                     "-i", url, "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                self.error = None
                while not self.stop_flag:
                    raw = p.stdout.read(16000)      # o secunda de audio
                    if not raw or len(raw) < 8000:
                        break
                    # pe rezerva directa (parola in linia de comanda) nu ramanem la nesfarsit:
                    # cand go2rtc raspunde din nou, ne intoarcem la el (aici nu se pierde nimic)
                    if idx != 0 and time.time() - started > 600:
                        import record
                        if record.go2rtc_are(f"cam{self.cam['id']}"):
                            revenire = True
                            break
                    a = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
                    rms = float(np.sqrt(np.mean(a * a)))
                    db = round(20 * float(np.log10(max(rms, 1e-7))), 1)
                    self._add(db)
            except Exception as e:
                self.error = str(e)[:80]
            finally:
                if p:
                    try:
                        p.kill()
                    except Exception:
                        pass
            if revenire or time.time() - started > 300:
                attempt = 0        # urmatoarea incercare: iar sursa preferata (go2rtc)
            if not self.stop_flag and not revenire:
                time.sleep(5)      # camera indisponibila -> reincearca

    def _add(self, db):
        now = time.time()
        self.level = db
        self.history.append((now, db))
        noise_stats.add(self.cam_id, db, now)
        recent = [d for t, d in self.history if now - t <= BASELINE_SEC]
        if len(recent) >= 20:
            self.baseline = round(statistics.median(recent), 1)
        self._check_rules(now, db)

    def _check_rules(self, now, db):
        import settings
        if not settings.get("noise_alarm_enabled"):
            self._loud_since = None
            return
        thr = settings.get("noise_threshold_db")          # prag absolut
        sustain = settings.get("noise_sustain_sec")       # cat trebuie sa tina
        spike = settings.get("noise_spike_db")            # crestere fata de fond

        motiv = None
        if db >= thr:
            self._loud_since = self._loud_since or now
            if now - self._loud_since >= sustain:
                motiv = f"zgomot sustinut {db} dB (peste {thr} dB de {int(now - self._loud_since)}s)"
        else:
            self._loud_since = None
        if (not motiv and self.baseline is not None
                and db - self.baseline >= spike):
            motiv = f"varf brusc: {db} dB, cu {round(db - self.baseline, 1)} dB peste fondul obisnuit"

        if motiv and now - self._last_alarm > COOLDOWN:
            self._last_alarm = now
            self._loud_since = None
            if self.on_event:
                try:
                    self.on_event(self.cam_id, motiv, db)
                except Exception as e:
                    logare.info(f"[audio] cam{self.cam_id}: {e}")

    def series(self, minutes=15):
        """Nivelurile din ultimele minute, pentru grafic."""
        now = time.time()
        return [{"t": int(t), "db": d} for t, d in self.history if now - t <= minutes * 60]


def sync(cams, on_event=None):
    """Porneste/opreste monitoarele pentru camerele cu microfon real."""
    want = {c["id"]: c for c in cams
            if (c.get("features") or {}).get("mic") and (c.get("features") or {}).get("audio_ok", True)}
    for cid in list(monitors):
        if cid not in want:
            monitors.pop(cid).stop_flag = True
    for cid, cam in want.items():
        if cid not in monitors:
            m = AudioMonitor(cam, on_event)
            monitors[cid] = m
            m.start()


def snapshot():
    """Starea curenta pentru interfata: nivel, fond si tendinta."""
    out = {}
    for cid, m in monitors.items():
        out[cid] = {"db": m.level, "baseline": m.baseline,
                    "puncte": len(m.history), "eroare": m.error}
    return out
