#!/usr/bin/env python3
"""Stratul de drivere: ce producator vorbeste camera si cum (Etapa 7, multi-vendor).

Pana acum ISAPI-ul Hikvision era cablat direct in ~55 de locuri (camctl.py, URL-uri RTSP,
poze, sanatate). Acum fiecare camera are `vendor` in registru (`hik` implicit; `onvif`
pentru restul), iar codul care conteaza pe producator trece pe aici:

  rtsp_main / rtsp_sub   URL-urile de flux (inregistrare, grila, HD, sanatate)
  snapshot               un JPEG la rezolutie mare (Face ID, confirmarea de intrus)
  device_info / ping     identificare + „mai raspunde?" (watchdog)
  probe_features         ce stie camera (mic, difuzor, sirena, strobo, releu, zoom, PTZ)
  ptz_move, presets_*    miscare continua + presetari
  set_output             releul
  trigger_siren, set_strobe, speak, get/set_stream, get/set_time
                         DOAR Hikvision; pe ONVIF ridica Nesuportat (interfata nu arata
                         butoanele, pentru ca probe_features le lasa false)
  events_watcher         alertStream-ul ISAPI; pe ONVIF nu exista (AI-ul acopera)

`hik` deleaga la camctl.py neschimbat (un singur loc cu ISAPI); `onvif` foloseste onvif.py.
Camera ONVIF isi tine URL-urile aflate la adaugare in `onvif: {main, sub, poza}` din
registru, ca sa nu intrebe camera la fiecare pornire de flux.
"""
import urllib.parse

VENDORI = {"hik": "Hikvision (ISAPI)", "onvif": "ONVIF (generic)", "demo": "Demo (flux sintetic, fără cameră)"}

# camerele demo: go2rtc genereaza singur imaginea (ffmpeg lavfi), fara nicio camera reala —
# interfata se poate vedea si testa pe o instalare goala, iar capturile publice nu arata
# casa nimanui. Tiparul (testsrc2, smptebars...) vine din cam["demo"].
DEMO_TIPARE = ("testsrc2", "testsrc", "smptebars", "life")   # fara mandelbrot: 135% CPU si bitrate urias


def este_demo(cam):
    return (cam or {}).get("vendor") == "demo"


def demo_sursa(cam, mare):
    tipar = (cam or {}).get("demo") if (cam or {}).get("demo") in DEMO_TIPARE else DEMO_TIPARE[0]
    size = "1280x720" if mare else "640x360"
    return f"ffmpeg:virtual?video={tipar}&size={size}&rate=15#video=h264"


class Nesuportat(RuntimeError):
    """Actiune pe care producatorul camerei nu o stie (ex. sirena pe ONVIF)."""


def vendor(cam):
    v = (cam or {}).get("vendor") or "hik"
    return v if v in VENDORI else "hik"


def este_hik(cam):
    return vendor(cam) == "hik"


# ---- fluxuri ----
def _cu_parola(url, cam):
    """Pune user:parola in URL-ul RTSP daca lipsesc (GetStreamUri le omite de obicei)."""
    try:
        u = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    if u.username or not cam.get("user"):
        return url
    pw = urllib.parse.quote(cam.get("pass") or "", safe="")
    host = u.hostname or cam["ip"]
    if u.port:
        host = f"{host}:{u.port}"
    return urllib.parse.urlunsplit((u.scheme, f"{cam['user']}:{pw}@{host}", u.path, u.query, u.fragment))


def rtsp_main(cam):
    if este_demo(cam):
        return demo_sursa(cam, True)
    if este_hik(cam):
        pw = urllib.parse.quote(cam["pass"], safe="")
        return f"rtsp://{cam['user']}:{pw}@{cam['ip']}:{cam.get('port', 554)}/Streaming/Channels/101"
    o = cam.get("onvif") or {}
    if not o.get("main"):
        raise Nesuportat("camera ONVIF fără URL de flux (readauga sau resondeaza camera)")
    return _cu_parola(o["main"], cam)


def rtsp_sub(cam):
    if este_demo(cam):
        return demo_sursa(cam, False)
    if este_hik(cam):
        pw = urllib.parse.quote(cam["pass"], safe="")
        return f"rtsp://{cam['user']}:{pw}@{cam['ip']}:{cam.get('port', 554)}/Streaming/Channels/102"
    o = cam.get("onvif") or {}
    return _cu_parola(o.get("sub") or o.get("main") or "", cam) or rtsp_main(cam)


def rtsp(cam, canal):
    """Pe numarul de canal Hikvision (101 main / 102 sub), pentru apelantii vechi."""
    return rtsp_main(cam) if int(canal) % 100 == 1 else rtsp_sub(cam)


# ---- identificare / sanatate ----
def _onvif(cam):
    if este_demo(cam):
        raise Nesuportat("camera demo nu are actiuni (flux sintetic)")
    import onvif
    return onvif.Camera(cam["ip"], cam.get("user") or "", cam.get("pass") or "",
                        (cam.get("onvif") or {}).get("port") or cam.get("http_port") or 80)


def device_info(cam):
    """{model, serial, firmware, name, manufacturer}; ridica RuntimeError la esec."""
    if este_demo(cam):
        return {"manufacturer": "OpenVigil", "model": "Demo", "serial": "", "firmware": "", "name": cam.get("name") or "Demo"}
    if este_hik(cam):
        import xml.etree.ElementTree as ET
        import requests
        from requests.auth import HTTPDigestAuth
        s = requests.Session()
        s.auth = HTTPDigestAuth(cam["user"], cam["pass"])
        r = s.get(f"http://{cam['ip']}/ISAPI/System/deviceInfo", timeout=12)
        if r.status_code == 401:
            raise RuntimeError("autentificare esuata (user/parola)")
        r.raise_for_status()
        root = ET.fromstring(r.text)
        g = lambda t: next((e.text for e in root.iter() if e.tag.endswith(t)), None)
        return {"manufacturer": "Hikvision", "model": g("model"), "serial": g("serialNumber"),
                "firmware": g("firmwareVersion"), "name": g("deviceName") or g("model")}
    i = _onvif(cam).info()
    return {**i, "name": i.get("model") or "ONVIF"}


def ping(cam, timeout=6):
    """True daca camera raspunde la o interogare ieftina (watchdog-ul online/offline)."""
    if este_demo(cam):
        return True
    try:
        if este_hik(cam):
            import requests
            from requests.auth import HTTPDigestAuth
            s = requests.Session()
            s.auth = HTTPDigestAuth(cam["user"], cam["pass"])
            return s.get(f"http://{cam['ip']}/ISAPI/System/deviceInfo", timeout=timeout).ok
        _onvif(cam).info()
        return True
    except Exception:
        return False


def snapshot(cam):
    """JPEG (bytes) de pe mainstream, sau None."""
    if este_demo(cam):
        return None                     # miniaturile vin oricum de la go2rtc (frame.jpeg)
    try:
        if este_hik(cam):
            import requests
            from requests.auth import HTTPDigestAuth
            s = requests.Session()
            s.auth = HTTPDigestAuth(cam["user"], cam["pass"])
            r = s.get(f"http://{cam['ip']}/ISAPI/Streaming/channels/101/picture", timeout=8)
            return r.content if r.ok and r.content[:2] == b"\xff\xd8" else None
        o = cam.get("onvif") or {}
        return _onvif(cam).poza(o.get("poza"))
    except Exception:
        return None


# ---- capabilitati ----
def probe_features(cam):
    if este_demo(cam):
        return {"mic": False, "talk": False, "siren": False, "strobe": False, "io_out": 0,
                "sounds": [], "zoom": False, "ptz": False, "focus": False, "smart": {}}
    if este_hik(cam):
        import camctl
        return camctl.probe_features(cam)
    c = _onvif(cam)
    f = {"mic": False, "talk": False, "siren": False, "strobe": False, "io_out": 0,
         "sounds": [], "zoom": False, "ptz": False, "focus": False, "smart": {}}
    try:
        f["io_out"] = len(c.relee())
    except Exception:
        pass
    try:
        serv = c.servicii()
        if serv.get("ptz"):
            f["zoom"] = True          # nu putem sti fara proba; detect_ptz confirma rotirea
            try:
                f["presets"] = bool(c.presetari()) or None
            except Exception:
                pass
    except Exception:
        pass
    return f


def descopera_fluxuri(cam):
    """Pentru o camera ONVIF noua: {main, sub, poza, port, profiluri}, de pus in cam['onvif']."""
    c = _onvif(cam)
    fl = c.fluxuri()
    return {"main": fl["main"], "sub": fl["sub"], "poza": fl["poza"], "port": c.port,
            "profiluri": [{k: p[k] for k in ("nume", "w", "h", "codec")} for p in fl["profiluri"]]}


def setup_camera(cam, feat):
    """Pregatirea dupa adaugare (audio AAC, linkage strobo): doar Hikvision."""
    if este_hik(cam):
        import camctl
        return camctl.setup_camera(cam, feat)
    return None


# ---- actiuni ----
def ptz_move(cam, pan=0, tilt=0, zoom=0):
    if este_hik(cam):
        import camctl
        return camctl.ptz_move(cam, pan=pan, tilt=tilt, zoom=zoom)
    return _onvif(cam).ptz_move(pan, tilt, zoom)


def presets_list(cam):
    if este_hik(cam):
        import camctl
        return camctl.presets_list(cam)
    return _onvif(cam).presetari()


def preset_save(cam, pid, nume):
    if este_hik(cam):
        import camctl
        return camctl.preset_save(cam, pid, nume)
    return _onvif(cam).presetare_salveaza(nume)


def _token_onvif(cam, pid):
    for p in _onvif(cam).presetari():
        if p["id"] == int(pid):
            return p["token"]
    raise Nesuportat("presetarea nu există")


def preset_goto(cam, pid):
    if este_hik(cam):
        import camctl
        return camctl.preset_goto(cam, pid)
    return _onvif(cam).presetare_du_te(_token_onvif(cam, pid))


def preset_delete(cam, pid):
    if este_hik(cam):
        import camctl
        return camctl.preset_delete(cam, pid)
    return _onvif(cam).presetare_sterge(_token_onvif(cam, pid))


def set_output(cam, port, on):
    if este_hik(cam):
        import camctl
        return camctl.set_output(cam, port, on)
    c = _onvif(cam)
    relee = c.relee()
    if not relee:
        raise Nesuportat("camera nu are relee ONVIF")
    tok = relee[max(0, min(len(relee) - 1, int(port) - 1))]
    return c.releu(tok, on)


def trigger_siren(cam, *a, **kw):
    if not este_hik(cam):
        raise Nesuportat("sirena există doar pe camerele Hikvision")
    import camctl
    return camctl.trigger_siren(cam, *a, **kw)


def set_strobe(cam, *a, **kw):
    if not este_hik(cam):
        raise Nesuportat("stroboscopul există doar pe camerele Hikvision")
    import camctl
    return camctl.set_strobe(cam, *a, **kw)


def speak(cam, *a, **kw):
    if not este_hik(cam):
        raise Nesuportat("difuzorul (two-way audio ISAPI) există doar pe camerele Hikvision")
    import camctl
    return camctl.speak(cam, *a, **kw)


def get_stream(cam, ch=101):
    if not este_hik(cam):
        return {}                        # ONVIF/demo: nimic de citit
    import camctl
    return camctl.get_stream(cam, ch)


def set_stream(cam, ch=101, **kw):
    if not este_hik(cam):
        raise Nesuportat("calitatea fluxului se seteaza doar pe camerele Hikvision (ISAPI)")
    import camctl
    return camctl.set_stream(cam, ch, **kw)


def get_time(cam):
    if este_hik(cam):
        import camctl
        return camctl.get_time(cam)
    import time
    t = _onvif(cam).ora()
    return {"mode": "onvif", "tz": "UTC", "local": t.isoformat() if t else "",
            "drift": round(t.timestamp() - time.time()) if t else None, "ntp": ""}


def set_time(cam, **kw):
    if not este_hik(cam):
        raise Nesuportat("sincronizarea orei se face doar pe camerele Hikvision")
    import camctl
    return camctl.set_time(cam, **kw)


def are_evenimente(cam):
    """Camera trimite evenimente proprii (alertStream ISAPI)? ONVIF nu, aici."""
    return este_hik(cam)
