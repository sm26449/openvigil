#!/usr/bin/env python3
"""Inregistrare continua pe disc pentru camerele standalone (n-au SD/HDD propriu).

Un proces ffmpeg per camera: mainstream RTSP copiat (fara transcodare) in segmente MP4 de
5 minute, data/recordings/cam<id>/YYYY-MM-DD_HH-MM-SSZ.mp4 — ora UTC, marcata cu Z.
Numele in ora locala (fara Z, fisierele de dinainte de 2026-10-23) se citesc in continuare,
dar nu se mai scriu: la trecerea la ora de iarna ora 03:00-03:59 vine de doua ori si al
doilea rand de segmente le suprascria pe primele. Interfata afiseaza tot ora locala.
Supervizare cu restart, retentie dupa zile + garda de spatiu liber.

Playback-ul/exportul folosesc concat demuxer (lista de fisiere + inpoint pentru pornire
exacta in interiorul unui segment).
"""
import calendar
import errno
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse

import config as C
import json
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REC_DIR = os.path.join(ROOT, "data", "recordings")
TMP_DIR = os.path.join(ROOT, "data", "tmp")
SEGMENT = 300  # secunde per fisier
SOCKET_TIMEOUT = 20   # secunde fara date pe socket -> ffmpeg iese singur
STALL_SEC = 90        # secunde fara scriere pe disc -> il omoram noi
# Pe o sursa de rezerva (direct de pe camera, cu parola in linia de comanda) nu ramanem la
# nesfarsit: dupa atatea secunde, daca go2rtc raspunde din nou, revenim la el — la inceputul
# unui segment, ca sa pierdem cat mai putin (segmentul inchis e intreg).
REVENIRE_SEC = 600
                      # (in mod normal fisierul se actualizeaza la 1-18 secunde)
FN_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})(Z?)\.mp4$")
FN_FMT = "%Y-%m-%d_%H-%M-%S"


def fn_epoch(fn):
    """Epoch-ul de start dintr-un nume de segment: cu Z = UTC, fara = ora locala (vechi).
    None daca numele nu e al unui segment."""
    m = FN_RE.match(fn)
    if not m:
        return None
    # fara strptime: formatul e fix (YYYY-MM-DD_HH-MM-SS), iar strptime ia un lock global si
    # sub GIL, cu zeci de fire, 27.000 de nume costau 20+ s per apel (healthz, watchdog)
    st = (int(fn[0:4]), int(fn[5:7]), int(fn[8:10]),
          int(fn[11:13]), int(fn[14:16]), int(fn[17:19]), 0, 0, -1)
    return calendar.timegm(st) if m[5] else time.mktime(st)


def _rtsp(cam):
    """Mainstream-ul camerei, dupa producator (drivers.py: Hikvision sau ONVIF)."""
    import drivers
    return drivers.rtsp_main(cam)


GO2RTC_RTSP = getattr(C, "GO2RTC_RTSP", "rtsp://127.0.0.1:8554")


def go2rtc_are(nume):
    """go2rtc-ul cunoaste fluxul `nume` (HTTP, fara dependinte noi)."""
    import urllib.request
    api = getattr(C, "GO2RTC_API", "http://127.0.0.1:1984")
    try:
        with urllib.request.urlopen(f"{api}/api/streams?src={nume}", timeout=3) as r:
            return r.status == 200 and b'"error"' not in r.read()
    except Exception:
        return False


def revenire_la_preferata(idx, ruleaza_de, acum, segment=SEGMENT, prag=REVENIRE_SEC):
    """Pur: e momentul sa parasim sursa de rezerva `idx` (0 = preferata) pentru a reveni la
    cea preferata? Doar dupa `prag` secunde si doar in primele 10 s ale unui segment nou."""
    return idx != 0 and acum - ruleaza_de > prag and int(acum) % segment < 10


def _sources(cam):
    """Sursele de inregistrare, perechi (url, eticheta), in ordinea preferintei:
    1. go2rtc local (`camN_main`, vezi gen_go2rtc.py): parola camerei ramane in go2rtc.yaml,
       nu in linia de comanda a ffmpeg-ului (`ps` o arata oricui e logat pe masina);
    2. direct de pe camera — rezerva cand go2rtc nu raspunde (porneste, a cazut), ca
       inregistrarea sa nu depinda de el;
    3. prin NVR, doar daca a fost importata de acolo si NVR-ul nu e retras.
    Fara rezerva, o camera care refuza o sursa (blocare temporara, parola schimbata) ar
    inceta sa inregistreze."""
    import settings
    urls = [(f"{GO2RTC_RTSP}/cam{cam['id']}_main", "prin go2rtc")]
    if cam.get("vendor") == "demo":
        return urls                      # fluxul sintetic exista doar in go2rtc
    urls.append((_rtsp(cam), "direct de pe camera"))
    if cam.get("nvr_channel") and not settings.nvr_retras():
        pw = urllib.parse.quote(C.NVR_PASS, safe="")
        urls.append((f"rtsp://{C.NVR_USER}:{pw}@{C.NVR_IP}:{C.NVR_PORT}"
                     f"/Streaming/Channels/{cam['nvr_channel']}01", "prin NVR"))
    return urls


class Recorder(threading.Thread):
    def __init__(self, cam):
        super().__init__(daemon=True, name=f"rec-cam{cam['id']}")
        self.cam = cam
        self.stop_flag = False
        self.proc = None
        self.stalls = 0          # de cate ori l-am gasit blocat si l-am repornit
        self.started_at = 0
        self.rec_fails = 0       # caderi rapide consecutive (pt. alerta la fail-loop)

    def run(self):
        d = os.path.join(REC_DIR, f"cam{self.cam['id']}")
        os.makedirs(d, exist_ok=True)
        attempt = 0
        while not self.stop_flag:
            # recalculate la fiecare incercare: comutatorul „NVR retras” se aplica pe loc,
            # fara repornirea serviciului
            urls = _sources(self.cam)
            idx = attempt % len(urls)
            url, eticheta = urls[idx]
            if attempt and len(urls) > 1:
                logare.info(f"[record] cam{self.cam['id']}: sursa {eticheta}")
            attempt += 1
            revenire = False
            # video copiat; audio re-codat AAC (G.711 de pe unele camere nu intra in MP4)
            cmd = ["ffmpeg", "-nostdin", "-loglevel", "error",
                   "-rtsp_transport", "tcp",
                   # Fara asta, un flux care se opreste fara sa inchida conexiunea lasa
                   # ffmpeg viu si mut la nesfarsit: procesul pare sanatos, dar nu se mai
                   # scrie nimic pe disc. Exact asa s-au pierdut 3h46m pe cam4.
                   "-timeout", str(SOCKET_TIMEOUT * 1_000_000), "-i", url,
                   "-map", "0", "-c:v", "copy", "-c:a", "aac", "-b:a", "64k",
                   "-f", "segment", "-segment_time", str(SEGMENT),
                   "-segment_atclocktime", "1", "-reset_timestamps", "1",
                   # MP4 fragmentat: fisierul in curs de scriere e deja redabil (moov-ul
                   # clasic s-ar scrie abia la inchidere -> ultimele 5 min ar fi moarte)
                   "-segment_format_options",
                   "movflags=+frag_keyframe+empty_moov+default_base_moof",
                   "-strftime", "1", os.path.join(d, FN_FMT + "Z.mp4")]
            started = time.time()
            try:
                # TZ=UTC doar pentru ffmpeg: -strftime numeste fisierele cu ora locala a
                # procesului, iar in ora locala 03:xx exista de doua ori in octombrie
                self.proc = subprocess.Popen(cmd, stderr=subprocess.DEVNULL,
                                             env={**os.environ, "TZ": "UTC"})
                self.started_at = started
                while self.proc.poll() is None:
                    if self.stop_flag:
                        self.proc.terminate()
                        try:
                            self.proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            self.proc.kill()
                        return
                    # Singura dovada ca inregistram e ca fisierul creste. Un proces viu
                    # nu inseamna nimic — pe cam4 ffmpeg a rulat 9 ore peste un gol de
                    # aproape 4. Daca discul nu s-a miscat, il omoram si o luam de la capat.
                    age = time.time() - (last_write(self.cam["id"]) or started)
                    if age > STALL_SEC and time.time() - started > STALL_SEC:
                        self.stalls += 1
                        logare.warning(f"[record] cam{self.cam['id']}: BLOCAT — nimic scris de "
                              f"{age:.0f}s, repornesc lantul")
                        self.proc.kill()
                        try:
                            self.proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            pass
                        break
                    if (revenire_la_preferata(idx, started, time.time())
                            and go2rtc_are(f"cam{self.cam['id']}_main")):
                        logare.info(f"[record] cam{self.cam['id']}: go2rtc a revenit, "
                                    f"parasesc sursa „{eticheta}”")
                        revenire = True
                        self.proc.terminate()
                        try:
                            self.proc.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            self.proc.kill()
                        break
                    time.sleep(5)
            except Exception as e:
                logare.info(f"[record] cam{self.cam['id']}: {e}")
            # o rulare scurta = cadere; N consecutive = fail-loop -> alerta (throttled), ca
            # sa nu piardem inregistrarea unei camere in tacere.
            lifetime = time.time() - started
            if lifetime < 60:
                self.rec_fails += 1
                if self.rec_fails == 5:
                    try:
                        import notify
                        notify.send(f"⚠️ Înregistrarea cam{self.cam['id']} pica in bucla "
                                    f"(5 caderi sub 60s) — verifica camera/reteaua",
                                    key=f"rec-fail-{self.cam['id']}")
                    except Exception as e:
                        logare.info(f"[record] cam{self.cam['id']} alerta fail: {str(e)[:60]}")
            else:
                self.rec_fails = 0
            # daca sursa curenta a mers bine mult timp, la urmatoarea cadere reincepem
            # tot cu cea directa (preferata), nu continuam alternarea
            if lifetime > 300 or revenire:
                attempt = 0
            if not self.stop_flag and not revenire:
                time.sleep(10)  # camera picata / eroare -> reincearca


recorders = {}


def semnatura(cam):
    """Ce conteaza pentru conexiunea de inregistrare: schimbarea oricarui camp cere un
    ffmpeg nou (inainte, o parola sau un IP nou ramaneau neaplicate pana la restart)."""
    o = cam.get("onvif") or {}
    return (cam.get("ip"), cam.get("port", 554), cam.get("user"), cam.get("pass"),
            cam.get("vendor") or "hik", o.get("main"))


def sync(cams):
    """Porneste/opreste recorderele pentru lista curenta de camere standalone; o camera a
    carei conexiune s-a schimbat isi reporneste recorderul cu datele noi."""
    ids = {c["id"] for c in cams}
    for cid in list(recorders):
        if cid not in ids:
            recorders.pop(cid).stop_flag = True
    for c in cams:
        r = recorders.get(c["id"])
        if r is not None and getattr(r, "sig", None) != semnatura(c):
            logare.warning(f"[record] cam{c['id']}: conexiunea s-a schimbat, repornesc recorderul")
            recorders.pop(c["id"]).stop_flag = True
            r = None
        if r is None:
            r = Recorder(c)
            r.sig = semnatura(c)
            recorders[c["id"]] = r
            r.start()


def archive_dir():
    """Nivelul de arhiva (setarea `arhiva_dir`): un al doilea disc unde se muta segmentele
    CU evenimente cand expira retentia primara, in loc sa se stearga. Gol = oprit."""
    import settings
    d = (settings.get("arhiva_dir") or "").strip()
    return d or None


def blocat():
    """True cand in directorul de inregistrari nu se poate scrie desi exista: volum montat
    read-only, permisiuni gresite pe volum sau director criptat (fscrypt pe gazda) fara cheie
    (ENOKEY). Director lipsa sau scriibil -> False."""
    proba = os.path.join(REC_DIR, ".proba-cheie")
    try:
        fd = os.open(proba, os.O_WRONLY | os.O_CREAT, 0o600)
    except OSError as e:
        return e.errno in (errno.ENOKEY, errno.EACCES, errno.EPERM, errno.EROFS)
    os.close(fd)
    try:
        os.remove(proba)
    except OSError:
        pass
    return False


_listare_cache = {}     # director -> (mtime_dir, [(epoch, path, fn)]); se invalideaza singur
_listare_lock = threading.Lock()


def _listeaza(d, cam_id):
    """Segmentele din directorul camerei, cu cache pe mtime-ul directorului: un fisier nou
    sau sters schimba mtime-ul (ext4), deci lista se reface doar cand chiar s-a schimbat.
    Apelata de zeci de ori pe minut (healthz, watchdog, continuitate, redare, lant)."""
    dd = os.path.join(d, f"cam{cam_id}")
    try:
        mt = os.path.getmtime(dd)
    except OSError:
        return []
    with _listare_lock:
        c = _listare_cache.get(dd)
        if c and c[0] == mt:
            return list(c[1])
    out = []
    try:
        names = os.listdir(dd)
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        return out
    for fn in names:
        t = fn_epoch(fn)
        if t is not None:
            out.append((t, os.path.join(dd, fn), fn))
    with _listare_lock:
        _listare_cache[dd] = (mt, out)
    return list(out)


def _files(cam_id, arhiva=True):
    """Fisierele unei camere, sortate: [(epoch_start, path, fname)]. Cu arhiva=True (implicit)
    intra si segmentele mutate pe nivelul de arhiva — redarea, exportul si acoperirea le vad
    ca pe oricare altele; retentia si supravegherea scrierii cer doar discul primar."""
    out = _listeaza(REC_DIR, cam_id)
    ad = archive_dir() if arhiva else None
    if ad:
        out += _listeaza(ad, cam_id)
    out.sort()
    return out


def last_write(cam_id):
    """Epoch-ul ultimei scrieri pe disc pentru camera. Numele fisierelor sunt marcaje de
    timp, deci cel mai nou fisier e ultimul dupa epoch — nu are rost sa dam stat pe toate.
    (Ordinea alfabetica nu mai ajunge: numele UTC si cele vechi, locale, se amesteca.)"""
    files = _files(cam_id, arhiva=False)
    if not files:
        return None
    try:
        return os.path.getmtime(files[-1][1])
    except OSError:
        return None


def has_recordings(cam_id):
    return bool(_files(cam_id))


_codec_cache = {}


def reset_codec(cam_id):
    """Dupa schimbarea codecului pe camera, uitam ce stiam despre inregistrari."""
    _codec_cache.pop(cam_id, None)


def video_codec(cam_id):
    """Codecul video al inregistrarilor camerei ("h264"/"hevc"), din ultimul segment."""
    if cam_id in _codec_cache:
        return _codec_cache[cam_id]
    files = _files(cam_id)
    if not files:
        return None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0",
                            files[-1][1]], capture_output=True, text=True, timeout=20)
        codec = r.stdout.strip() or None
    except subprocess.TimeoutExpired:
        codec = None
    if codec:
        _codec_cache[cam_id] = codec
    return codec


def list_segments(cam_id, day):
    """Segmentele unei zile (YYYY-MM-DD), unite in intervale continue, format ca ISAPI:
    [{"start": iso, "end": iso, "type": "continuous"}]."""
    local_day = lambda ts: time.strftime("%Y-%m-%d", time.localtime(ts))
    files = [(t, p, fn) for t, p, fn in _files(cam_id) if local_day(t) == day]
    now = time.time()
    runs = []
    for t, p, fn in files:
        end = min(t + SEGMENT, now)
        if runs and t - runs[-1][1] < 15:  # lipite (tolereaza restarturi scurte)
            runs[-1][1] = end
        else:
            runs.append([t, end])
    iso = lambda ts: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime(ts))
    return [{"start": iso(a), "end": iso(b), "type": "continuous"} for a, b in runs]


def build_playlist(cam_id, start_ts, end_ts=None):
    """Scrie fisierul concat (cu inpoint in primul segment) pentru redare/export de la
    start_ts (epoch). Intoarce calea listei sau None daca nu exista inregistrari."""
    files = _files(cam_id)
    sel = [(t, p) for t, p, _ in files if t + SEGMENT > start_ts
           and (end_ts is None or t < end_ts)]
    if not sel:
        return None
    os.makedirs(TMP_DIR, exist_ok=True)
    path = os.path.join(TMP_DIR, f"play_{cam_id}_{int(start_ts)}_{int(end_ts or 0)}.txt")
    with open(path, "w") as f:
        for i, (t, p) in enumerate(sel):
            f.write(f"file '{p}'\n")
            if i == 0 and start_ts > t:
                f.write(f"inpoint {start_ts - t:.2f}\n")
            if end_ts and i == len(sel) - 1 and end_ts < t + SEGMENT:
                f.write(f"outpoint {end_ts - t:.2f}\n")
    return path


GOL_MIN = 15   # sub atat intre doua segmente e doar granita dintre fisiere, nu un gol


def goluri(cam_id, start_ts, end_ts):
    """Golurile de inregistrare din [start_ts, end_ts): [(de_la, pana_la)] epoch, inclusiv
    lipsa materialului la inceput/sfarsit. Lista concat lipeste segmentele cap la cap, deci
    un gol INTOARCE timpul in clip: filigranul (pts:localtime) ar arata o ora gresita, iar
    timeline-ul s-ar desincroniza — apelantii refuza filigranul peste un gol sau il declara."""
    sel = sorted((t, t + SEGMENT) for t, _, _ in _files(cam_id) if t + SEGMENT > start_ts and t < end_ts)
    out = []
    cursor = start_ts
    for a, b in sel:
        if a - cursor > GOL_MIN:
            out.append((cursor, a))
        cursor = max(cursor, b)
    if end_ts - cursor > GOL_MIN:
        out.append((cursor, end_ts))
    return out


def harvest_frames(out_dir, interval=60):
    """Extrage cate un cadru la `interval` secunde din toate inregistrarile existente,
    in out_dir (nume compatibile cu data/dataset: cam<id>-YYYYMMDD-HHMMSS.jpg).
    Idempotent — sare peste cadrele deja extrase. Intoarce cate cadre noi a scris."""
    os.makedirs(out_dir, exist_ok=True)
    made = 0
    now = time.time()
    for name in sorted(os.listdir(REC_DIR) if os.path.isdir(REC_DIR) else []):
        if not name.startswith("cam"):
            continue
        cid = name[3:]
        for t, p, _ in _files(int(cid)):
            usable = min(SEGMENT, max(0, now - t - 2))
            for off in range(0, int(usable), interval):
                ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(t + off))
                out = os.path.join(out_dir, f"cam{cid}-{ts}.jpg")
                if os.path.exists(out):
                    continue
                try:
                    r = subprocess.run(
                        ["ffmpeg", "-y", "-nostdin", "-loglevel", "error",
                         "-ss", str(off), "-i", p, "-frames:v", "1", "-q:v", "3", out],
                        capture_output=True, timeout=60)
                    if r.returncode == 0 and os.path.exists(out):
                        made += 1
                except subprocess.TimeoutExpired:
                    pass
    return made


def _prune_arhiva(now=None):
    """Retentia nivelului de arhiva: `arhiva_zile` dupa inregistrare, plus garda de spatiu
    `arhiva_min_free_gb` (cele mai vechi primele). Intoarce cate a sters."""
    import settings
    ad = archive_dir()
    if not ad or not os.path.isdir(ad):
        return 0
    now = now or time.time()
    v_zile = settings.get("arhiva_zile")
    zile = 180 if v_zile is None else int(v_zile)
    toate = []
    try:
        cams = [int(n[3:]) for n in os.listdir(ad) if n.startswith("cam") and n[3:].isdigit()]
    except OSError:
        return 0
    sterse = 0
    for cid in cams:
        for t, p, fn in _listeaza(ad, cid):
            if now - t > zile * 86400:
                try:
                    os.remove(p)
                    sterse += 1
                except OSError:
                    pass
            else:
                toate.append((t, p))
    toate.sort()
    try:
        v = settings.get("arhiva_min_free_gb")            # 0 inseamna „fara garda”, nu „implicit”
        min_free = (20.0 if v is None else float(v)) * 2**30
        while toate and shutil.disk_usage(ad).free < min_free:
            os.remove(toate.pop(0)[1])
            sterse += 1
    except OSError:
        pass
    if sterse:
        logare.warning(f"[record] arhiva: {sterse} segmente expirate sterse")
    return sterse


MOTION_PAD = 30  # secunde pastrate inainte/dupa un eveniment, in modul "la miscare"
SNAP_DIR = os.path.join(ROOT, "data", "snapshots")
SNAP_RE = re.compile(r"^(\d{6})_cam(\d+)_")


def event_times(cam_id, days=3):
    """Momentele evenimentelor unei camere (epoch), citite din numele snapshot-urilor —
    marcaje persistente, vizibile si din serviciul de inregistrare."""
    out = []
    cutoff = time.time() - days * 86400
    try:
        for day in os.listdir(SNAP_DIR):
            try:
                base = time.mktime(time.strptime(day, "%Y-%m-%d"))
            except ValueError:
                continue
            if base < cutoff - 86400:
                continue
            for fn in os.listdir(os.path.join(SNAP_DIR, day)):
                m = SNAP_RE.match(fn)
                if m and int(m[2]) == cam_id:
                    hh, mm, ss = int(m[1][:2]), int(m[1][2:4]), int(m[1][4:])
                    out.append(base + hh * 3600 + mm * 60 + ss)
    except FileNotFoundError:
        pass
    return out


def prune_motion(cam_id, event_times):
    """Modul 'la miscare': pastreaza doar segmentele care ating un eveniment (+/- padding).
    Inregistram continuu si stergem dupa — asa nu pierdem nimic dintr-un eveniment
    (pre-roll gratuit) si evitam pornirea/oprirea repetata a ffmpeg-ului.
    event_times = epoch-uri ale evenimentelor camerei. Segmentul in curs nu se atinge."""
    files = _files(cam_id, arhiva=False)
    if not files or not event_times:
        return 0
    ev = sorted(event_times)
    now = time.time()
    # SIGURANTA: daca nu am primit NICIUN eveniment in ultimele ore desi inregistram,
    # inseamna ca detectia e picata (camera nereachable, ISAPI blocat, watcher mort) —
    # in acest caz NU stergem nimic, altfel am rade inregistrarile fara sa stim.
    newest_seg = files[-1][0]
    if newest_seg - max(ev) > 3 * 3600:
        logare.info(f"[record] cam{cam_id}: fara evenimente de peste 3h — nu sterg nimic "
              f"(verifica detectia pe camera)")
        return 0
    removed = 0
    import bisect
    for t, p, _ in files[:-1]:  # ultimul = segmentul care se scrie acum
        if now - t < SEGMENT * 2:
            continue  # prea proaspat: evenimentele pot inca sa apara
        a, b = t - MOTION_PAD, t + SEGMENT + MOTION_PAD
        i = bisect.bisect_left(ev, a)
        if i < len(ev) and ev[i] <= b:
            continue  # atinge un eveniment -> se pastreaza
        try:
            os.remove(p)
            removed += 1
        except OSError:
            pass
    return removed


def _cam_retention(cam_id):
    """Zilele de pastrare pentru o camera: setarea globala, cu posibilitate de exceptie
    per camera (campul "retention" din registru)."""
    import settings
    days = settings.get("retention_days")
    ev_days = settings.get("retention_event_days")
    try:
        with open(os.path.join(ROOT, "data", "cameras.json")) as f:
            for c in json.load(f):
                if c["id"] == cam_id and isinstance(c.get("retention"), dict):
                    days = c["retention"].get("days", days)
                    ev_days = c["retention"].get("event_days", ev_days)
    except (FileNotFoundError, ValueError, KeyError):
        pass
    return days, max(ev_days, days)   # evenimentele nu se pastreaza mai putin decat restul


def prune():
    """Retentie in doua trepte: segmentele care contin evenimente traiesc mai mult decat
    cele goale. Daca spatiul liber scade sub prag, stergem intai segmentele fara
    evenimente (cele mai vechi), si abia apoi pe cele cu evenimente. Reprogramat orar.

    Reprogramarea e in FINALLY: cu Timer-ul pus abia la coada functiei, o singura
    exceptie neprinsa omora curatarea pentru totdeauna, in tacere — s-a intamplat:
    discul a ajuns la 100% (18 GB liberi, segmente de 11 zile la retentie de 7)
    pentru ca lantul murise candva dupa 13 august."""
    try:
        _prune_body()
    except Exception as e:
        logare.error(f"[record] CURATAREA A ESUAT: {e}")
        try:
            import notify
            notify.send(f"⚠️ Curatarea inregistrarilor a eșuat ({str(e)[:80]}) — "
                        "reincerc peste o ora", key="prune-esuat")
        except Exception:
            pass
    finally:
        t = threading.Timer(3600, prune)
        t.daemon = True
        t.start()


def _arhiveaza(cid, t, p, fn):
    """Muta un segment cu evenimente pe nivelul de arhiva (copie + verificare hash +
    stergere). Intoarce True daca a ajuns acolo; la orice problema fisierul ramane unde e."""
    ad = archive_dir()
    if not ad:
        return False
    try:
        import lant
        dest_dir = os.path.join(ad, f"cam{cid}")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, fn)
        tmp = dest + ".part"
        shutil.copyfile(p, tmp)
        amprenta = lant.get(cid, fn)
        if lant.hash_fisier(tmp) != (amprenta["sha256"] if amprenta else lant.hash_fisier(p)):
            os.remove(tmp)
            logare.info(f"[record] arhiva cam{cid} {fn}: copia nu are acelasi hash — ramane pe primar")
            return False
        os.replace(tmp, dest)
        os.remove(p)
        if amprenta:
            lant.muta(cid, fn, dest)
        return True
    except OSError as e:
        logare.info(f"[record] arhiva cam{cid} {fn}: {str(e)[:80]}")
        return False


def _prune_body():
    import bisect
    import settings
    now = time.time()
    keep_ev, keep_plain = [], []
    arhivate = 0
    try:
        cams = [int(n[3:]) for n in os.listdir(REC_DIR) if n.startswith("cam")]
    except FileNotFoundError:
        cams = []
    for cid in cams:
        days, ev_days = _cam_retention(cid)
        ev = sorted(event_times(cid, days=ev_days + 1))
        for t, p, fn in _files(cid, arhiva=False):
            a, b = t - MOTION_PAD, t + SEGMENT + MOTION_PAD
            i = bisect.bisect_left(ev, a)
            has_event = i < len(ev) and ev[i] <= b
            # cu nivel de arhiva, segmentele cu evenimente parasesc primarul dupa
            # `retention_days` (ca cele goale) — dar se MUTA, nu se sterg; traiesc acolo
            # `arhiva_zile`. Fara arhiva: ca inainte, ev_days pe primar.
            if has_event and archive_dir() and now - t > days * 86400:
                if _arhiveaza(cid, t, p, fn):
                    arhivate += 1
                    continue
            limit = (ev_days if has_event else days) * 86400
            if now - t > limit:
                try:
                    os.remove(p)
                except OSError:
                    pass
            else:
                (keep_ev if has_event else keep_plain).append((t, p))
    if arhivate:
        logare.info(f"[record] {arhivate} segmente mutate in arhiva")
    _prune_arhiva(now)
    # garda de spatiu: intai cele fara evenimente, cele mai vechi
    keep_plain.sort()
    keep_ev.sort()
    fortat = 0     # cate segmente am sters DESI erau mai tinere decat retentia promisa
    try:
        min_free = settings.get("retention_min_free_gb") * 2**30
        for bucket in (keep_plain, keep_ev):
            while bucket and shutil.disk_usage(REC_DIR).free < min_free:
                try:
                    t, p = bucket.pop(0)
                    # un segment cu evenimente scos de garda de spatiu merge in arhiva, nu la gunoi
                    if bucket is keep_ev and _arhiveaza(int(os.path.basename(os.path.dirname(p))[3:]),
                                                        t, p, os.path.basename(p)):
                        continue
                    os.remove(p)
                    fortat += 1
                except OSError:
                    pass
    except FileNotFoundError:
        pass
    # Astea NU expirasera — le-a taiat lipsa de spatiu. Adica retentia scrisa in setari
    # nu se respecta, iar asta e exact genul de lucru care nu trebuie sa treaca in tacere.
    if fortat:
        msg = (f"Spatiu insuficient: am sters {fortat} segmente care nu expirasera. "
               f"Retentia configurata nu se poate respecta.")
        logare.info(f"[record] {msg}")
        try:
            import notify
            notify.send("⚠️ " + msg, key="retentie-fortata")
        except Exception:
            pass
    # curata listele concat vechi
    try:
        for fn in os.listdir(TMP_DIR):
            p = os.path.join(TMP_DIR, fn)
            # DOAR fisiere: un director aparut in tmp (s-a intamplat: model-eval)
            # facea os.remove sa arunce si omora tot lantul de curatare
            if os.path.isfile(p) and time.time() - os.path.getmtime(p) > 86400:
                os.remove(p)
    except FileNotFoundError:
        pass
