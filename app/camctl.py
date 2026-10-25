#!/usr/bin/env python3
"""Control camere Hikvision standalone: capabilitati (microfon/difuzor/sirena/stroboscop/releu),
actiuni manuale si evenimente de alarma in timp real (alertStream).

Stroboscopul nu are endpoint de declansare manuala pe firmware-ul IPC G2 (doar sirena are
/AudioAlarm/<id>/test). Folosim trucul standard: linkage whiteLight pe evenimentul IO-1 +
comutarea intrarii de alarma (nefolosita fizic) intre NO si NC prin ISAPI. Cu nimic conectat,
NC = intrare permanent "activa" -> stroboscopul palpaie; NO = se stinge.
"""
import collections
import re
import threading
import time
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

import snaps
import logare

HIK_NS = "http://www.hikvision.com/ver20/XMLSchema"
ET.register_namespace("", HIK_NS)


def _sess(cam):
    s = requests.Session()
    s.auth = HTTPDigestAuth(cam["user"], cam["pass"])
    return s


def _url(cam, path):
    return f"http://{cam['ip']}{path}"


# ---- deploy linii/zone pe camera (ISAPI Smart) ----
# Camera impune linia/zona la rezolutie NATIVA si declanseaza alarma (pe care o
# confirmam apoi cu AI). Coordonate Hik: 0-1000, Y in SUS (Cartesian) — cadrul nostru
# e 0-1 cu Y in JOS, deci inversam Y.
def _hik_xy(x, y):
    return (max(0, min(1000, round(x * 1000))),
            max(0, min(1000, round((1 - y) * 1000))))


def _coords_xml(pts, tag):
    out = []
    for x, y in pts:
        hx, hy = _hik_xy(x, y)
        out.append(f"<{tag}><positionX>{hx}</positionX><positionY>{hy}</positionY></{tag}>")
    return "".join(out)


def deploy_smart(cam, lines, zones, channel=1):
    """Trimite liniile (LineDetection) si zonele (FieldDetection) pe camera. Maxim 4
    din fiecare (limita uzuala Hik). Intoarce lista [(ce, status_code, ok)]."""
    s = _sess(cam)
    rez = []
    # FieldDetection pe camera cere minim 4 colturi (triunghiul e respins cu 400)
    zone_scurte = [z.get("name") for z in zones if len(z.get("poly", [])) < 4]
    zones = [z for z in zones if len(z.get("poly", [])) >= 4][:4]
    lines = lines[:4]
    items = ""
    for i, ln in enumerate(lines, 1):
        items += (f"<LineItem><id>{i}</id><enabled>true</enabled>"
                  f"<sensitivityLevel>50</sensitivityLevel>"
                  f"<directionSensitivity>{ln.get('sens_cam', 'any')}</directionSensitivity>"
                  f"<CoordinatesList>{_coords_xml([ln['p1'], ln['p2']], 'Coordinates')}"
                  f"</CoordinatesList></LineItem>")
    xml_line = ('<?xml version="1.0" encoding="UTF-8"?>'
                f'<LineDetection><id>1</id><enabled>{"true" if lines else "false"}</enabled>'
                '<normalizedScreenSize><normalizedScreenWidth>1000</normalizedScreenWidth>'
                '<normalizedScreenHeight>1000</normalizedScreenHeight></normalizedScreenSize>'
                f'<LineItemList size="{len(lines)}">{items}</LineItemList></LineDetection>')
    regs = ""
    for i, z in enumerate(zones, 1):
        regs += ('<FieldDetectionRegion version="2.0" '
                 'xmlns="http://www.hikvision.com/ver20/XMLSchema">'
                 f'<id>{i}</id><enabled>true</enabled><sensitivityLevel>50</sensitivityLevel>'
                 '<timeThreshold>0</timeThreshold>'
                 f'<RegionCoordinatesList>{_coords_xml(z["poly"], "RegionCoordinates")}'
                 '</RegionCoordinatesList></FieldDetectionRegion>')
    xml_field = ('<?xml version="1.0" encoding="UTF-8"?>'
                 '<FieldDetection version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">'
                 f'<id>1</id><enabled>{"true" if zones else "false"}</enabled>'
                 '<normalizedScreenSize><normalizedScreenWidth>1000</normalizedScreenWidth>'
                 '<normalizedScreenHeight>1000</normalizedScreenHeight></normalizedScreenSize>'
                 f'<FieldDetectionRegionList size="{len(zones)}">{regs}</FieldDetectionRegionList>'
                 '</FieldDetection>')
    for ce, path, body in [("linii", "LineDetection", xml_line),
                           ("zone", "FieldDetection", xml_field)]:
        try:
            r = s.put(_url(cam, f"/ISAPI/Smart/{path}/{channel}"), data=body.encode(),
                      headers={"Content-Type": "application/xml"}, timeout=12)
            ok = r.ok and ("<statusString>OK</statusString>" in r.text or "<subStatusCode>ok" in r.text
                           or r.status_code == 200)
            rez.append((ce, r.status_code, ok))
        except Exception as e:
            rez.append((ce, "eroare", str(e)[:50]))
    return {"rezultat": rez, "zone_ignorate": zone_scurte}


def set_gop(cam, sec=1.0, channel=101):
    """Seteaza intervalul de cadre-cheie (GovLength) la ~sec secunde. GOP mic = keyframe
    des = vederea live porneste rapid, fara ecranul verde de asteptare; in schimb
    inregistrarile cresc putin. Preia configul, schimba DOAR GovLength, il pune inapoi.
    Intoarce (ok, gop_setat_sau_mesaj)."""
    s = _sess(cam)
    try:
        r = s.get(_url(cam, f"/ISAPI/Streaming/channels/{channel}"), timeout=10)
        if not r.ok:
            return False, f"GET {r.status_code}"
        fps_m = re.search(r"<maxFrameRate>(\d+)</maxFrameRate>", r.text)
        fps = (int(fps_m.group(1)) / 100) if fps_m else 15
        gop = max(5, round(fps * sec))
        xml = re.sub(r"<GovLength>\d+</GovLength>", f"<GovLength>{gop}</GovLength>", r.text, count=1)
        rp = s.put(_url(cam, f"/ISAPI/Streaming/channels/{channel}"),
                   data=xml.encode(), headers={"Content-Type": "application/xml"}, timeout=12)
        return (rp.ok and "OK" in rp.text), (gop if rp.ok else rp.status_code)
    except Exception as e:
        return False, str(e)[:50]


# ---- capabilitati ----
def probe_features(cam):
    """Interogheaza camera si intoarce ce stie sa faca:
    mic (audio pe stream), talk (difuzor/two-way audio), siren (+ lista de sunete),
    strobe (lumina alba de alarma), io_out (numar relee)."""
    s = _sess(cam)
    f = {"mic": False, "talk": False, "siren": False, "strobe": False,
         "io_out": 0, "sounds": []}
    try:
        r = s.get(_url(cam, "/ISAPI/System/TwoWayAudio/channels"), timeout=8)
        f["talk"] = r.ok and "<TwoWayAudioChannel" in r.text
    except requests.RequestException:
        pass
    try:
        r = s.get(_url(cam, "/ISAPI/System/Audio/channels"), timeout=8)
        f["mic"] = r.ok and "<enabled>true</enabled>" in r.text
    except requests.RequestException:
        pass
    try:
        r = s.get(_url(cam, "/ISAPI/Event/triggers/notifications/AudioAlarm/capabilities?format=json"),
                  timeout=8)
        if r.ok and r.text.lstrip().startswith("{"):
            cap = r.json().get("AudioAlarmCap", {})
            f["sounds"] = [{"id": a["audioID"], "name": a["audioDescription"]}
                           for a in cap.get("audioTypeListCap", [])]
            f["siren"] = bool(f["sounds"])
    except (requests.RequestException, ValueError):
        pass
    try:
        r = s.get(_url(cam, "/ISAPI/Event/triggers/notifications/whiteLightAlarm/capabilities?format=json"),
                  timeout=8)
        f["strobe"] = r.ok and r.text.lstrip().startswith("{")
    except requests.RequestException:
        pass
    try:
        r = s.get(_url(cam, "/ISAPI/System/IO/outputs"), timeout=8)
        if r.ok:
            f["io_out"] = r.text.count("<IOOutputPort ")
    except requests.RequestException:
        pass
    try:  # lentila motorizata (zoom optic): camerele fixe raspund "notSupport"
        r = s.get(_url(cam, "/ISAPI/PTZCtrl/channels/1/capabilities"), timeout=8)
        f["zoom"] = r.ok and "<ContinuousZoomSpace>" in r.text
        # "ptz" NU se ia de aici: si bullet-urile fixe raporteaza ContinuousPanTiltSpace,
        # pentru un cap RS-485 extern care de obicei nu exista. Se afla prin proba
        # (detect_ptz), pornita din interfata.
        try:
            rf = s.get(_url(cam, "/ISAPI/Image/channels/1/focusConfiguration"), timeout=6)
            f["focus"] = rf.ok and "<focusStyle>" in rf.text
        except requests.RequestException:
            f["focus"] = False
    except requests.RequestException:
        pass
    f["smart"] = probe_smart(cam)
    return f


# ---- functii "smart" ale camerei (detectie in firmware, independenta de AI-ul nostru) ----
# cheie -> (eticheta RO, endpoint ISAPI)
SMART = {
    "motion": ("Miscare", "/ISAPI/System/Video/inputs/channels/1/motionDetection"),
    "line": ("Trecere linie", "/ISAPI/Smart/LineDetection/1"),
    "field": ("Intruziune zona", "/ISAPI/Smart/FieldDetection/1"),
    "enter": ("Intrare in zona", "/ISAPI/Smart/regionEntrance/1"),
    "exit": ("Iesire din zona", "/ISAPI/Smart/regionExiting/1"),
    "scene": ("Schimbare scena", "/ISAPI/Smart/SceneChangeDetection/1"),
}
SMART_CAP = {"line": "LineDetection", "field": "FieldDetection",
             "enter": "RegionEntrance", "exit": "RegionExiting",
             "scene": "SceneChangeDetection"}


def probe_smart(cam):
    """Ce functii smart suporta camera si care sunt pornite: {cheie: True/False}."""
    s = _sess(cam)
    out = {}
    caps = ""
    try:
        r = s.get(_url(cam, "/ISAPI/Smart/capabilities"), timeout=8)
        caps = r.text if r.ok else ""
    except requests.RequestException:
        pass
    for key, (_, path) in SMART.items():
        if key != "motion" and f"isSupport{SMART_CAP[key]}>true" not in caps:
            continue
        try:
            r = s.get(_url(cam, path), timeout=8)
            if r.ok and "<enabled>" in r.text:
                out[key] = r.text.split("<enabled>")[1].split("<")[0] == "true"
        except requests.RequestException:
            pass
    return out


def set_smart(cam, key, on):
    """Porneste/opreste o functie smart, pastrand restul configuratiei camerei."""
    if key not in SMART:
        raise ValueError("functie necunoscută")
    path = SMART[key][1]
    s = _sess(cam)
    r = s.get(_url(cam, path), timeout=10)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    el = root.find(f"{{{HIK_NS}}}enabled")
    if el is None:
        el = next((e for e in root.iter() if e.tag.endswith("enabled")), None)
    if el is None:
        raise RuntimeError("camera nu expune comutatorul")
    el.text = "true" if on else "false"
    r = s.put(_url(cam, path), data=ET.tostring(root, encoding="unicode"),
              headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()


# ---- setup idempotent dupa adaugare ----
def setup_camera(cam, feat):
    """Pregateste camera pentru interfata: audio AAC pornit pe ambele streamuri (altfel nu se
    aude microfonul in browser — MSE nu suporta G.711) si linkage-ul stroboscopului pe IO-1."""
    if feat.get("mic"):
        for ch in (101, 102):
            try:
                _enable_audio(cam, ch)
            except (requests.RequestException, ET.ParseError) as e:
                logare.info(f"[camctl] {cam['ip']}: audio pe stream {ch}: {e}")
        # unele modele refuza AAC in tacere si raman pe G.711; frontend-ul alege modul
        # dupa codecul efectiv (AAC se aude doar prin MSE, G.711 doar prin WebRTC)
        try:
            r = _sess(cam).get(_url(cam, "/ISAPI/Streaming/channels/102"), timeout=8)
            feat["aac"] = "<audioCompressionType>AAC<" in r.text
        except requests.RequestException:
            feat["aac"] = False
        # "mic" e doar declaratia camerei; unele au intrare de linie fara microfon
        # conectat, deci fluxul nu contine audio. Verificam efectiv.
        try:
            import health
            hc = health.check_stream(cam, 102)
            feat["audio_ok"] = bool(hc.get("ok")) and _has_audio(cam)
        except Exception:
            feat["audio_ok"] = False
    if feat.get("strobe"):
        try:
            _ensure_strobe_linkage(cam)
        except (requests.RequestException, ET.ParseError) as e:
            logare.info(f"[camctl] {cam['ip']}: linkage stroboscop: {e}")


def _has_audio(cam):
    """Fluxul chiar contine o pista audio? (intrare de linie fara microfon = nu)"""
    import subprocess, urllib.parse
    pw = urllib.parse.quote(cam["pass"], safe="")
    url = f"rtsp://{cam['user']}:{pw}@{cam['ip']}:{cam.get('port', 554)}/Streaming/Channels/102"
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-rtsp_transport", "tcp",
                            "-select_streams", "a:0", "-show_entries", "stream=codec_name",
                            "-of", "csv=p=0", "-timeout", "8000000", url],
                           capture_output=True, timeout=20)
        return bool(r.stdout.strip())
    except Exception:
        return False


def _enable_audio(cam, ch):
    """Activeaza audio AAC pe un stream (101/102) daca nu e deja."""
    s = _sess(cam)
    r = s.get(_url(cam, f"/ISAPI/Streaming/channels/{ch}"), timeout=10)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    audio = root.find(f".//{{{HIK_NS}}}Audio")
    if audio is None:
        return
    enabled = audio.find(f"{{{HIK_NS}}}enabled")
    codec = audio.find(f"{{{HIK_NS}}}audioCompressionType")
    if enabled.text == "true" and codec is not None and codec.text == "AAC":
        return
    enabled.text = "true"
    if codec is not None:
        codec.text = "AAC"
    r = s.put(_url(cam, f"/ISAPI/Streaming/channels/{ch}"),
              data=ET.tostring(root, encoding="unicode"),
              headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()


def _ensure_strobe_linkage(cam):
    """Adauga notificarea whiteLight pe evenimentul IO-1 (pastrand ce era deja)."""
    s = _sess(cam)
    r = s.get(_url(cam, "/ISAPI/Event/triggers/IO-1"), timeout=10)
    r.raise_for_status()
    if "whiteLight" in r.text:
        return
    root = ET.fromstring(r.text)
    lst = root.find(f"{{{HIK_NS}}}EventTriggerNotificationList")
    if lst is None:
        lst = ET.SubElement(root, f"{{{HIK_NS}}}EventTriggerNotificationList")
    n = ET.SubElement(lst, f"{{{HIK_NS}}}EventTriggerNotification")
    for tag, val in (("id", "whiteLight"), ("notificationMethod", "whiteLight"),
                     ("notificationRecurrence", "beginning")):
        ET.SubElement(n, f"{{{HIK_NS}}}{tag}").text = val
    r = s.put(_url(cam, "/ISAPI/Event/triggers/IO-1"),
              data=ET.tostring(root, encoding="unicode"),
              headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()


# ---- actiuni ----
def trigger_siren(cam, audio_id=None, volume=None, times=None, custom=False):
    """Reda un sunet prin difuzorul camerei (o data, implicit).
    custom=True -> unul dintre cele 3 sloturi cu sunete proprii (incarcate din interfata
    camerei); altfel, unul dintre cele 12 sunete din firmware."""
    s = _sess(cam)
    r = s.get(_url(cam, "/ISAPI/Event/triggers/notifications/AudioAlarm?format=json"), timeout=10)
    r.raise_for_status()
    cfg = r.json()["AudioAlarm"]
    if custom:
        cfg["audioClass"] = "customAudio"
        cfg["customAudioID"] = int(audio_id or 1)
        cfg["alarmTimes"] = int(times or 1)
        if volume is not None:
            cfg["audioVolume"] = max(1, min(100, int(volume)))
        if not any(w.get("TimeRange") for w in cfg.get("TimeRangeList", [])):
            cfg["TimeRangeList"] = [
                {"week": w, "TimeRange": [{"id": 1, "beginTime": "00:00", "endTime": "24:00"}]}
                for w in range(1, 8)]
        s.put(_url(cam, "/ISAPI/Event/triggers/notifications/AudioAlarm?format=json"),
              json={"AudioAlarm": cfg}, timeout=10).raise_for_status()
        r = s.put(_url(cam, f"/ISAPI/Event/triggers/notifications/AudioAlarm/"
                            f"{cfg['customAudioID']}/test?format=json"), json={}, timeout=10)
        r.raise_for_status()
        return
    # fara orar de armare sunetul nu se reda; daca e gol (sters din greseala), il refacem 24/7
    if not any(w.get("TimeRange") for w in cfg.get("TimeRangeList", [])):
        cfg["TimeRangeList"] = [
            {"week": w, "TimeRange": [{"id": 1, "beginTime": "00:00", "endTime": "24:00"}]}
            for w in range(1, 8)]
    aid = int(audio_id or cfg.get("audioID", 1))
    cfg["audioID"] = aid
    cfg["alarmTimes"] = int(times or 1)
    if volume is not None:
        cfg["audioVolume"] = max(1, min(100, int(volume)))
    # sunetele 1..11 exista si in lista alertAudio; test-ul foloseste audioID din URL,
    # dar volumul/repetarile vin din configuratie -> o scriem inainte
    if aid <= 11:
        cfg["audioClass"] = "alertAudio"
        cfg["alertAudioID"] = aid
    r = s.put(_url(cam, "/ISAPI/Event/triggers/notifications/AudioAlarm?format=json"),
              json={"AudioAlarm": cfg}, timeout=10)
    r.raise_for_status()
    r = s.put(_url(cam, f"/ISAPI/Event/triggers/notifications/AudioAlarm/{aid}/test?format=json"),
              json={}, timeout=10)
    r.raise_for_status()


_strobe_timers = {}

INPUT_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<IOInputPort version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<id>1</id><enabled>true</enabled><triggering>{trig}</triggering><name></name>
</IOInputPort>"""


def set_strobe(cam, on, auto_off=60):
    """Porneste/opreste stroboscopul prin trucul NO/NC. auto_off = siguranta (secunde)."""
    s = _sess(cam)
    r = s.put(_url(cam, "/ISAPI/System/IO/inputs/1"),
              data=INPUT_TMPL.format(trig="low" if on else "high"),
              headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()
    t = _strobe_timers.pop(cam["id"], None)
    if t:
        t.cancel()
    if on and auto_off:
        t = threading.Timer(auto_off, set_strobe, args=(cam, False, 0))
        t.daemon = True
        t.start()
        _strobe_timers[cam["id"]] = t


IOPORT_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<IOPortData version="2.0" xmlns="http://www.hikvision.com/ver20/XMLSchema">
<outputState>{state}</outputState>
</IOPortData>"""


def set_output(cam, port, on):
    """Comuta releul (iesirea de alarma) al camerei."""
    s = _sess(cam)
    r = s.put(_url(cam, f"/ISAPI/System/IO/outputs/{port}/trigger"),
              data=IOPORT_TMPL.format(state="high" if on else "low"),
              headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()


def get_stream(cam, ch=101):
    """Setarile de calitate ale unui stream: codec, rezolutie, bitrate, fps + ce suporta."""
    s = _sess(cam)
    r = s.get(_url(cam, f"/ISAPI/Streaming/channels/{ch}"), timeout=10)
    r.raise_for_status()
    t = r.text

    def g(tag, default=""):
        return t.split(f"<{tag}>")[1].split("<")[0] if f"<{tag}>" in t else default

    codecs = []
    try:
        c = s.get(_url(cam, f"/ISAPI/Streaming/channels/{ch}/capabilities"), timeout=10).text
        if 'videoCodecType opt="' in c:
            codecs = c.split('videoCodecType opt="')[1].split('"')[0].split(",")
    except requests.RequestException:
        pass
    fps = int(g("maxFrameRate", "0") or 0) // 100
    kbps = int(g("vbrUpperCap", "0") or 0)
    return {"codec": g("videoCodecType"), "codecs": codecs,
            "width": int(g("videoResolutionWidth", "0") or 0),
            "height": int(g("videoResolutionHeight", "0") or 0),
            "kbps": kbps, "fps": fps,
            "keyframe": int(g("keyFrameInterval", "0") or 0),  # ms intre cadrele-cheie
            "gb_day": round(kbps * 0.0108, 1)}  # kbps -> GB/zi la debit maxim


def set_stream(cam, ch=101, codec=None, kbps=None, fps=None, keyframe=None):
    """Schimba calitatea stream-ului (codec/bitrate/fps/keyframe), pastrand restul.
    keyframe = milisecunde intre cadrele-cheie: mai mic => pornire/reconectare mai rapida,
    dar bitrate ceva mai mare (conteaza pe mainstream, e neglijabil pe substream)."""
    s = _sess(cam)
    r = s.get(_url(cam, f"/ISAPI/Streaming/channels/{ch}"), timeout=10)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    v = root.find(f"{{{HIK_NS}}}Video")
    if v is None:
        raise RuntimeError("stream fără sectiune Video")

    def setel(tag, val):
        el = v.find(f"{{{HIK_NS}}}{tag}")
        if el is not None:
            el.text = str(val)

    if codec:
        setel("videoCodecType", codec)
    if kbps:
        setel("vbrUpperCap", int(kbps))
        setel("constantBitRate", int(kbps))
    if fps:
        setel("maxFrameRate", int(fps) * 100)
    if keyframe:
        # Firmware-ul scrie efectiv GovLength (lungimea GOP in CADRE); keyFrameInterval
        # (ms) e doar reflectarea lui si e ignorat la scriere pe multe modele.
        el = v.find(f"{{{HIK_NS}}}maxFrameRate")
        eff_fps = int(fps) if fps else (int(el.text) // 100 if el is not None and el.text else 20)
        setel("keyFrameInterval", int(keyframe))
        setel("GovLength", max(1, round(eff_fps * int(keyframe) / 1000)))
    r = s.put(_url(cam, f"/ISAPI/Streaming/channels/{ch}"),
              data=ET.tostring(root, encoding="unicode"),
              headers={"Content-Type": "application/xml"}, timeout=15)
    r.raise_for_status()


def play_twoway(cam, pcm_mulaw, block_sec=4.0, max_sec=120):
    """Trimite audio prin canalul de vorbire al camerei (difuzor / iesire de linie).

    Camera accepta un SINGUR bloc per sesiune (al doilea da 403), deci reluam ciclul
    deschide -> trimite -> inchide pentru fiecare bucata. Overhead masurat: ~0.85s.
    pcm_mulaw = audio brut G.711 u-law, 8 kHz mono (8000 octeti = 1 secunda).
    """
    s = _sess(cam)
    base = _url(cam, "/ISAPI/System/TwoWayAudio/channels/1")
    step = int(block_sec * 8000)
    sent = 0
    try:
        for i in range(0, min(len(pcm_mulaw), max_sec * 8000), step):
            chunk = pcm_mulaw[i:i + step]
            s.put(base + "/close", timeout=8)
            r = s.put(base + "/open", timeout=8)
            if not r.ok:
                break
            r = s.put(base + "/audioData", data=chunk,
                      headers={"Content-Type": "application/octet-stream"}, timeout=30)
            if not r.ok:
                break
            sent += len(chunk)
            time.sleep(len(chunk) / 8000.0)  # asteptam cat dureaza redarea bucatii
    finally:
        try:
            s.put(base + "/close", timeout=8)
        except requests.RequestException:
            pass
    return sent / 8000.0


def to_mulaw(path_or_bytes):
    """Converteste orice sursa audio in G.711 u-law 8 kHz mono (formatul canalului)."""
    import subprocess
    src = path_or_bytes if isinstance(path_or_bytes, str) else "pipe:0"
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", src,
                        "-ar", "8000", "-ac", "1", "-f", "mulaw", "-"],
                       input=None if isinstance(path_or_bytes, str) else path_or_bytes,
                       capture_output=True, timeout=120)
    return p.stdout


def speak(cam, text, repeat=1, speed=150):
    """Sintetizeaza textul (espeak-ng, romana) si il reda prin difuzorul camerei.
    Implementarea unica de TTS — folosita de interfata, de API-ul de automatizari
    si de motorul de reguli. Intoarce durata redarii in secunde."""
    import os
    import subprocess
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    tmp = os.path.join(root, "data", "tmp")
    os.makedirs(tmp, exist_ok=True)
    raw = os.path.join(tmp, f"say{cam['id']}_raw.wav")
    wav = os.path.join(tmp, f"say{cam['id']}.wav")
    # "--" inchide optiunile: un text care incepe cu "-" (ex. "-f /etc/passwd") ar fi
    # altfel interpretat de espeak ca optiune, nu rostit
    subprocess.run(["espeak-ng", "-v", "ro", "-s", str(int(speed)), "-p", "40",
                    "-w", raw, "--", str(text)[:300]], check=True, timeout=30)
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", raw,
                    "-ar", "8000", "-ac", "1", wav], check=True, timeout=30)
    if repeat > 1:
        loop = os.path.join(tmp, f"say{cam['id']}_loop.wav")
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-stream_loop", str(int(repeat) - 1), "-i", wav, loop],
                       check=True, timeout=60)
        wav = loop
    return play_twoway(cam, to_mulaw(wav))


PTZ_TMPL = """<PTZData><pan>0</pan><tilt>0</tilt><zoom>{z}</zoom></PTZData>"""


def ptz_move(cam, pan=0, tilt=0, zoom=0):
    """Miscare continua: fiecare axa -100..100, 0 = stop. Se opreste trimitand toate zero.

    Atentie: firmware-ul accepta pan/tilt cu 200 chiar si pe camerele care NU se rotesc
    (bullet cu lentila motorizata, unde interfata PTZ e pentru un cap RS-485 extern).
    De aceea capabilitatea "ptz" nu se ia din XML-ul de capabilitati, ci se masoara —
    vezi detect_ptz()."""
    lim = lambda v: max(-100, min(100, int(v)))
    s = _sess(cam)
    r = s.put(_url(cam, "/ISAPI/PTZCtrl/channels/1/continuous"),
              data=(f"<PTZData><pan>{lim(pan)}</pan><tilt>{lim(tilt)}</tilt>"
                    f"<zoom>{lim(zoom)}</zoom></PTZData>"),
              headers={"Content-Type": "application/xml"}, timeout=8)
    r.raise_for_status()


def ptz_zoom(cam, speed):
    """Zoom optic continuu: speed -100..100 (0 = stop)."""
    ptz_move(cam, zoom=speed)


def presets_list(cam):
    """Presetarile salvate in camera: [{"id": 1, "nume": "Poarta"}].
    Pe camerele fara cap mobil lista ramane mereu goala, chiar daca salvarea da 200."""
    s = _sess(cam)
    r = s.get(_url(cam, "/ISAPI/PTZCtrl/channels/1/presets"), timeout=10)
    r.raise_for_status()
    out = []
    for el in ET.fromstring(r.text):
        pid = el.find(f"{{{HIK_NS}}}id")
        nm = el.find(f"{{{HIK_NS}}}presetName")
        if pid is not None:
            out.append({"id": int(pid.text), "nume": (nm.text if nm is not None else "") or f"Pozitia {pid.text}"})
    return sorted(out, key=lambda p: p["id"])


def preset_save(cam, pid, nume):
    s = _sess(cam)
    r = s.put(_url(cam, f"/ISAPI/PTZCtrl/channels/1/presets/{int(pid)}"),
              data=(f"<PTZPreset><enabled>true</enabled><id>{int(pid)}</id>"
                    f"<presetName>{_xml_escape(nume)}</presetName></PTZPreset>"),
              headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()


def preset_goto(cam, pid):
    s = _sess(cam)
    r = s.put(_url(cam, f"/ISAPI/PTZCtrl/channels/1/presets/{int(pid)}/goto"),
              data="<PTZData/>", headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()


def preset_delete(cam, pid):
    s = _sess(cam)
    r = s.delete(_url(cam, f"/ISAPI/PTZCtrl/channels/1/presets/{int(pid)}"), timeout=10)
    r.raise_for_status()


def _xml_escape(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))[:32]


FOCUS_MODES = ("AUTO", "SEMIAUTO", "MANUAL")


def get_focus_mode(cam):
    """Cum focalizeaza camera: AUTO, SEMIAUTO sau MANUAL."""
    s = _sess(cam)
    r = s.get(_url(cam, "/ISAPI/Image/channels/1/focusConfiguration"), timeout=10)
    r.raise_for_status()
    el = ET.fromstring(r.text).find(f"{{{HIK_NS}}}focusStyle")
    return el.text if el is not None else None


def set_focus_mode(cam, mode):
    """Pe MANUAL, camera nu refocalizeaza dupa zoom si imaginea poate ramane neclara.
    SEMIAUTO focalizeaza dupa fiecare miscare si apoi sta linistita — de obicei alegerea
    buna la o camera fixa; AUTO cauta continuu, ceea ce la un cadru static e inutil."""
    mode = str(mode).upper()
    if mode not in FOCUS_MODES:
        raise ValueError("mod de focalizare necunoscut")
    s = _sess(cam)
    r = s.put(_url(cam, "/ISAPI/Image/channels/1/focusConfiguration"),
              data=('<FocusConfiguration><focusStyle>' + mode + '</focusStyle>'
                    '</FocusConfiguration>'),
              headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()
    return mode


def detect_ptz(cam, frame_fn):
    """Se roteste camera cu adevarat? Singurul raspuns de incredere e sa incercam.

    Firmware-ul Hikvision raporteaza ContinuousPanTiltSpace si pe bullet-urile cu lentila
    motorizata, unde interfata PTZ exista pentru un cap RS-485 extern care de obicei nu e
    montat: comanda intoarce 200 si nu se misca nimic. Masurat pe doua camere reale, pan a
    dat diferenta de imagine 1.0 pe una (cat zgomotul de fond) si 52.6 pe cealalta.

    Ca sa nu lasam o camera intoarsa daca proba reuseste, memoram intai pozitia intr-o
    presetare si ne intoarcem la ea. Daca presetarea nu se poate salva, nu incercam
    miscarea deloc — mai bine raportam "nu stim" decat sa stricam un unghi reglat cu mana.

    frame_fn() -> octeti JPEG. Intoarce {"ptz": bool, "presets": bool}.
    """
    import io
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return {"ptz": False, "presets": False}

    def dif(a, b):
        ia = Image.open(io.BytesIO(a)).convert("L").resize((160, 120))
        ib = Image.open(io.BytesIO(b)).convert("L").resize((160, 120))
        return sum(ImageChops.difference(ia, ib).getdata()) / (160 * 120)

    SLOT = 250      # presetare temporara, departe de cele folosite de om
    try:
        preset_save(cam, SLOT, "temp")
        are_presetari = any(p["id"] == SLOT for p in presets_list(cam))
    except requests.RequestException:
        are_presetari = False
    if not are_presetari:
        return {"ptz": False, "presets": False}

    try:
        a = frame_fn()
        time.sleep(1.5)
        zgomot = dif(a, frame_fn())          # cat difera doua cadre fara sa se miste nimic
        inainte = frame_fn()
        ptz_move(cam, pan=60)
        time.sleep(1.5)
        ptz_move(cam)
        time.sleep(2.5)
        misca = dif(inainte, frame_fn()) > max(6, zgomot * 3)
    except Exception:
        misca = False
    finally:
        try:
            preset_goto(cam, SLOT)
            time.sleep(4)
            preset_delete(cam, SLOT)
        except requests.RequestException:
            pass
    return {"ptz": bool(misca), "presets": True}


def set_speaker_volume(cam, vol):
    """Volumul difuzorului (two-way audio), 0..100."""
    s = _sess(cam)
    r = s.get(_url(cam, "/ISAPI/System/TwoWayAudio/channels/1"), timeout=10)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    el = root.find(f"{{{HIK_NS}}}speakerVolume")
    if el is None:
        return
    el.text = str(max(0, min(100, int(vol))))
    r = s.put(_url(cam, "/ISAPI/System/TwoWayAudio/channels/1"),
              data=ET.tostring(root, encoding="unicode"),
              headers={"Content-Type": "application/xml"}, timeout=10)
    r.raise_for_status()


# ---- evenimente de alarma (alertStream) ----
ACTIVE_TTL = 8  # camera repeta evenimentul ~1/s cat e activ; il consideram stins dupa 8s

# evenimentele "smart" de perimetru pe care le confirmam cu AI (linie / intrare in zona)
PERIMETRU = {"linedetection", "fielddetection", "regionEntrance", "regionExiting"}
_RO_INTRUS = {"person": "persoana", "car": "masina", "truck": "camion",
              "bus": "autobuz", "motorcycle": "motocicleta"}

# Cooldown pe intrus per (camera, eticheta): ACELASI om care lucreaza prin curte
# declansa perimetrul iar si iar — 144 din 220 de alerte intr-o saptamana veneau
# la <5 min dupa precedenta, pe aceeasi camera (masurat 11-21 sept). Prima alerta
# ramane instanta; repetitiile in fereastra tac (prezenta continua e oricum
# acoperita de episoade, care spun si cat a stat si ce a facut).
INTRUS_COOLDOWN = 300
_intrus_last = {}


def _intrus_cooldown_ok(cam_id, lbl, now=None):
    """Pur-testabil: e prima alerta pentru (camera, eticheta) din fereastra?
    Consuma fereastra doar cand raspunde True."""
    now = now or time.time()
    k = (cam_id, lbl)
    if now - _intrus_last.get(k, 0.0) < INTRUS_COOLDOWN:
        return False
    _intrus_last[k] = now
    return True


def _confirm_intrus(cam_id, ev, tstamp):
    """Camera a semnalat trecere/intrare la perimetru; verificam cu YOLO pe cadrul HD
    daca e OM/vehicul (intrus real) sau doar pom/vant (fals). Doar cele reale devin
    eveniment 'intrus' — cu poza HD dovada. Asa scapam de ~90% alarme false.
    Modul per camera (settings.intrus_mode) filtreaza ce escaladeaza: implicit doar
    oamenii (vehiculele = trafic pe camerele de intrare); 'strict' = orice."""
    import settings
    mode = (settings.get("intrus_mode") or {}).get(str(cam_id), "persoane")
    if mode == "off":
        return
    if mode == "noapte":
        try:
            import rules
            if not rules._is_night():
                return
        except Exception as e:
            logare.error(f"[camctl] verificarea de noapte a esuat ({str(e)[:60]}) — "
                  f"continui ca si cum ar fi noapte")
    # confirmarea (YOLO + gating Face ID) ruleaza in nvr-ai cand serviciul e viu —
    # modelele stau acolo (#9 pas 2); la esec de transport, local (modele lazy in web)
    gasit = adnotat = img = nume = None
    try:
        import ai_client
        if ai_client.remote():
            d = ai_client.detect_confirm(cam_id)
            if d is not None:
                gasit, nume = d.get("gasit") or {}, d.get("nume")
                if d.get("adnotat"):
                    import base64
                    import cv2
                    import numpy as np
                    adnotat = cv2.imdecode(
                        np.frombuffer(base64.b64decode(d["adnotat"]), np.uint8),
                        cv2.IMREAD_COLOR)
        if gasit is None:
            import detect
            gasit, adnotat, img = detect.confirm_alarm(cam_id)
    except Exception as e:
        logare.info(f"[camctl] confirmare cam{cam_id}: {str(e)[:60]}")
        return
    if mode == "persoane":
        gasit = {"person": gasit["person"]} if "person" in gasit else {}  # doar oameni
    if not gasit:
        return                 # nimic relevant pt. modul camerei -> suprimat (alarma ramane info)
    lbl = "person" if "person" in gasit else max(gasit, key=gasit.get)
    # gating Face ID: o PERSOANA cunoscuta (familie) nu e intrus — doar consemnare
    # (pe calea remote, nume vine gata calculat din nvr-ai)
    if lbl == "person" and nume is None and img is not None:
        try:
            import faces
            nume = faces.known_face(img)
        except Exception:
            nume = None
    if lbl == "person":
        if nume:
            try:
                import hooks
                hooks.emit("persoana", cam_id, nume=nume, context="perimetru")
            except Exception:
                pass
            logare.info(f"[camctl] cam{cam_id}: persoana cunoscuta ({nume}) la perimetru — "
                  f"nu e intrus")
            return
    if not _intrus_cooldown_ok(cam_id, lbl):
        return                 # aceeasi prezenta, re-declansata — prima alerta a spus deja
    if adnotat is not None:
        try:
            snaps.save_frame(cam_id, "intrus", adnotat)
        except Exception as e:
            # poza e proba incidentului — daca se pierde, macar sa se stie
            logare.info(f"[camctl] poza de intrus cam{cam_id} nesalvata: {str(e)[:60]}")
    try:
        import hooks
        import detect
        hooks.emit("intrus", cam_id, eticheta=lbl, eticheta_ro=_RO_INTRUS.get(lbl, lbl),
                   declansator=ev, incredere=gasit.get(lbl),
                   ce=", ".join(f"{_RO_INTRUS.get(k, k)}×1" for k in gasit),
                   model=detect.amprenta())
    except Exception:
        pass


class EventWatcher(threading.Thread):
    """Tine deschis /ISAPI/Event/notification/alertStream si urmareste evenimentele active."""

    def __init__(self, cam):
        super().__init__(daemon=True, name=f"events-{cam['ip']}")
        self.cam = cam
        self.stop_flag = False
        self.active = {}                          # eventType -> ultima aparitie (epoch)
        self.recent = collections.deque(maxlen=50)  # istoricul tranzitiilor inactive->active

    def run(self):
        fails = 0
        while not self.stop_flag:
            try:
                self._listen()
                fails = 0
            except Exception as e:
                # alertStream mort = zero evenimente de perimetru de pe camera asta, fara ca
                # cineva sa stie. Logam (rar, ca sa nu spamam) in loc de 'pass' silentios.
                fails += 1
                if fails in (1, 5, 20) or fails % 60 == 0:
                    logare.error(f"[camctl] cam{self.cam.get('id', '?')} alertStream esuat "
                          f"(x{fails}): {str(e)[:80]}")
            if not self.stop_flag:
                time.sleep(5)

    def _listen(self):
        s = _sess(self.cam)
        with s.get(_url(self.cam, "/ISAPI/Event/notification/alertStream"),
                   stream=True, timeout=(10, 90)) as r:
            r.raise_for_status()
            buf = ""
            for chunk in r.iter_content(chunk_size=2048):
                if self.stop_flag:
                    return
                buf += chunk.decode("utf-8", "ignore")
                while "</EventNotificationAlert>" in buf:
                    part, _, buf = buf.partition("</EventNotificationAlert>")
                    i = part.rfind("<EventNotificationAlert")
                    if i >= 0:
                        self._handle(part[i:] + "</EventNotificationAlert>")
                if len(buf) > 65536:  # gunoi multipart intre evenimente
                    buf = buf[-4096:]

    def _handle(self, xml):
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return
        ev = state = tstamp = None
        for el in root.iter():
            tag = el.tag.split("}", 1)[-1]
            if tag == "eventType":
                ev = el.text
            elif tag == "eventState":
                state = el.text
            elif tag == "dateTime":
                tstamp = el.text
        if not ev or state != "active" or ev == "videoloss":
            return
        now = time.time()
        if ev not in self.active or now - self.active[ev] > ACTIVE_TTL:
            self.recent.appendleft({"type": ev, "time": tstamp})
            snaps.capture(self.cam["id"], ev)  # poza pentru tabul Alarme (async, cu cooldown)
            try:
                import hooks
                hooks.emit("alarma_camera", self.cam["id"], tip=ev, moment_camera=tstamp)
            except Exception:
                pass
            # perimetru: confirmam cu AI (om/vehicul real vs pom/vant) — async, pe HD
            if ev in PERIMETRU:
                threading.Thread(target=_confirm_intrus,
                                 args=(self.cam["id"], ev, tstamp), daemon=True).start()
        self.active[ev] = now


watchers = {}


def sync_watchers(cams):
    """Porneste/opreste watcherele ca sa reflecte lista camerelor standalone."""
    import drivers
    cams = [c for c in cams if drivers.are_evenimente(c)]   # alertStream = doar Hikvision
    ids = {c["id"] for c in cams}
    for cid in list(watchers):
        if cid not in ids:
            watchers.pop(cid).stop_flag = True
    for c in cams:
        if c["id"] not in watchers:
            w = EventWatcher(c)
            watchers[c["id"]] = w
            w.start()


def events_snapshot():
    """Starea curenta pentru frontend: {cam_id: {active: [...], recent: [...]}}."""
    now = time.time()
    out = {}
    for cid, w in watchers.items():
        out[cid] = {
            "active": [t for t, ts in w.active.items() if now - ts < ACTIVE_TTL],
            "recent": list(w.recent)[:20],
        }
    return out


# ---- ora camerei: fus orar, NTP, sincronizare ----
def _tz_string(offset_seconds):
    """Fusul in formatul Hikvision: semnul e INVERSAT fata de UTC (POSIX).
    Romania vara (UTC+3) -> 'CST-3:00:00'."""
    total = int(offset_seconds)
    sign = "-" if total >= 0 else "+"
    total = abs(total)
    return f"CST{sign}{total // 3600}:{(total % 3600) // 60:02d}:00"


def get_time(cam):
    """Ora camerei, fusul ei si abaterea fata de ceasul serverului (secunde)."""
    import datetime
    s = _sess(cam)
    r = s.get(_url(cam, "/ISAPI/System/time"), timeout=8)
    r.raise_for_status()
    g = lambda k: (r.text.split(f"<{k}>")[1].split("<")[0]
                   if f"<{k}>" in r.text else "")
    lt = g("localTime")
    drift = None
    try:
        drift = datetime.datetime.fromisoformat(lt).timestamp() - time.time()
    except ValueError:
        pass
    ntp = ""
    try:
        n = s.get(_url(cam, "/ISAPI/System/time/ntpServers"), timeout=6).text
        for tag in ("hostName", "ipAddress"):
            if f"<{tag}>" in n:
                ntp = n.split(f"<{tag}>")[1].split("<")[0]
                break
    except requests.RequestException:
        pass
    return {"mode": g("timeMode"), "tz": g("timeZone"), "local": lt,
            "drift": round(drift) if drift is not None else None, "ntp": ntp}


def set_time(cam, ntp_host=None, use_ntp=True):
    """Aduce camera la ora corecta: fusul luat din ceasul serverului (deci si ora de vara
    se rezolva singura la re-sincronizare), ora setata direct, apoi NTP pentru mentinere."""
    import datetime
    s = _sess(cam)
    now = datetime.datetime.now().astimezone()
    tz = _tz_string(now.utcoffset().total_seconds())

    if ntp_host:   # serverul de timp, daca a fost dat
        # XML-ul construit EXPLICIT, nu mutat: intrarea existenta pe camere avea doar
        # <hostName> (time.windows.com), iar mutarea incerca sa scrie <ipAddress> —
        # element inexistent -> PUT incoerent pe care firmware-ul il ignora TACUT
        # (si raspunsul nu era verificat). Asa au ramas toate camerele pe serverul
        # Microsoft in loc de chrony-ul local (gasit 2026-09-10, cam11 la -59s).
        e_ip = ntp_host.replace(".", "").isdigit()
        adresa = (f"<ipAddress>{ntp_host}</ipAddress>" if e_ip
                  else f"<hostName>{ntp_host}</hostName>")
        body = ('<NTPServer version="2.0" '
                'xmlns="http://www.hikvision.com/ver20/XMLSchema">'
                '<id>1</id>'
                f'<addressingFormatType>{"ipaddress" if e_ip else "hostname"}'
                '</addressingFormatType>'
                f'{adresa}<portNo>123</portNo>'
                '<synchronizeInterval>10</synchronizeInterval></NTPServer>')
        try:
            r = s.put(_url(cam, "/ISAPI/System/time/ntpServers/1"), data=body,
                      headers={"Content-Type": "application/xml"}, timeout=10)
            r.raise_for_status()
        except requests.RequestException as e:
            logare.info(f"[camctl] server NTP pe {cam.get('ip')}: {e}")

    # 1. fus + ora exacta, in mod manual (merge si fara acces la internet)
    root = ET.fromstring(s.get(_url(cam, "/ISAPI/System/time"), timeout=8).text)
    for el in root.iter():
        t = el.tag.split("}")[-1]
        if t == "timeMode":
            el.text = "manual"
        elif t == "timeZone":
            el.text = tz
        elif t == "localTime":
            el.text = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S") + now.strftime("%z")[:3] + ":00"
    r = s.put(_url(cam, "/ISAPI/System/time"),
              data=ET.tostring(root, encoding="unicode"),
              headers={"Content-Type": "application/xml"}, timeout=12)
    r.raise_for_status()

    # 2. trecem pe NTP ca sa ramana sincronizata (daca poate ajunge la server)
    if use_ntp:
        time.sleep(0.5)
        root = ET.fromstring(s.get(_url(cam, "/ISAPI/System/time"), timeout=8).text)
        for el in root.iter():
            if el.tag.endswith("timeMode"):
                el.text = "NTP"
        s.put(_url(cam, "/ISAPI/System/time"),
              data=ET.tostring(root, encoding="unicode"),
              headers={"Content-Type": "application/xml"}, timeout=12)
    return tz
