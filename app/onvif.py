#!/usr/bin/env python3
"""Client ONVIF minimal (SOAP peste HTTP cu WS-Security UsernameToken), fara biblioteci noi.

Acopera ce ii trebuie NVR-ului de la o camera non-Hikvision: identificare
(GetDeviceInformation), serviciile (GetServices/GetCapabilities), profilurile si URL-urile
RTSP (GetProfiles + GetStreamUri), poza (GetSnapshotUri), PTZ continuu + presetari, releele
(DeviceIO) si ora. Evenimentele (PullPoint) NU: detectia AI pe fluxul video acopera oricum,
iar fiecare producator le implementeaza altfel.

Autentificarea: PasswordDigest = Base64(SHA1(nonce + created + parola)) — cere ceasuri
apropiate (camerele ONVIF refuza un `Created` cu > 5 min decalaj); unele camere accepta si
HTTP Digest, pe care il incercam a doua oara.
"""
import base64
import datetime
import hashlib
import os
import re
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

from i18n import t

TIMEOUT = 8
NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "tmd": "http://www.onvif.org/ver10/deviceIO/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
    "wsse": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd",
    "wsu": "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd",
}


class EroareOnvif(RuntimeError):
    pass


def _antet_securitate(user, pw):
    nonce = os.urandom(16)
    created = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + pw.encode()).digest()).decode()
    return (f'<s:Header><wsse:Security s:mustUnderstand="1" xmlns:wsse="{NS["wsse"]}" xmlns:wsu="{NS["wsu"]}">'
            f'<wsse:UsernameToken><wsse:Username>{_esc(user)}</wsse:Username>'
            f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>'
            f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{base64.b64encode(nonce).decode()}</wsse:Nonce>'
            f'<wsu:Created>{created}</wsu:Created></wsse:UsernameToken></wsse:Security></s:Header>')


def _esc(t):
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _plic(user, pw, corp):
    return (f'<?xml version="1.0" encoding="UTF-8"?><s:Envelope xmlns:s="{NS["s"]}" '
            f'xmlns:tds="{NS["tds"]}" xmlns:trt="{NS["trt"]}" xmlns:tptz="{NS["tptz"]}" '
            f'xmlns:tmd="{NS["tmd"]}" xmlns:tt="{NS["tt"]}">'
            f'{_antet_securitate(user, pw) if user else ""}<s:Body>{corp}</s:Body></s:Envelope>')


def _local(tag):
    return tag.split("}", 1)[-1]


def gaseste(root, nume):
    """Primul element cu numele local `nume` (fara namespace), sau None."""
    for el in root.iter():
        if _local(el.tag) == nume:
            return el
    return None


def toate(root, nume):
    return [el for el in root.iter() if _local(el.tag) == nume]


def text(root, nume, implicit=None):
    el = gaseste(root, nume)
    return el.text.strip() if el is not None and el.text else implicit


def apel(url, user, pw, corp, timeout=TIMEOUT, run=None):
    """Trimite un apel SOAP; intoarce radacina XML a raspunsului sau ridica EroareOnvif."""
    run = run or requests.post
    hdr = {"Content-Type": "application/soap+xml; charset=utf-8"}
    try:
        r = run(url, data=_plic(user, pw, corp).encode(), headers=hdr, timeout=timeout)
        if r.status_code == 401 and user:
            # a doua incercare: HTTP Digest (unele camere nu stiu WS-Security)
            r = run(url, data=_plic(None, None, corp).encode(), headers=hdr, timeout=timeout,
                    auth=HTTPDigestAuth(user, pw))
    except requests.RequestException as e:
        raise EroareOnvif(t("nu răspunde: {e}", e=str(e)[:100]))
    if r.status_code == 401:
        raise EroareOnvif(t("autentificare esuata (user/parola)"))
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        raise EroareOnvif(t("răspuns ne-SOAP (HTTP {cod})", cod=r.status_code))
    fault = gaseste(root, "Fault")
    if fault is not None or not r.ok:
        motiv = text(fault, "Text", "") or text(fault, "Reason", "") or f"HTTP {r.status_code}"
        if r.status_code == 401 or "NotAuthorized" in r.text or "Unauthorized" in r.text:
            raise EroareOnvif(t("autentificare esuata (user/parola)"))
        raise EroareOnvif(t("SOAP: {motiv}", motiv=motiv[:120]))
    return root


class Camera:
    """O camera ONVIF: ip[:port], user, parola. Adresele serviciilor se afla o data."""

    def __init__(self, ip, user, pw, port=80, run=None):
        self.ip, self.user, self.pw, self.port = ip, user, pw, int(port or 80)
        self.run = run
        self.dev = f"http://{ip}:{self.port}/onvif/device_service"
        self._xaddr = {}

    def _apel(self, url, corp):
        return apel(url, self.user, self.pw, corp, run=self.run)

    # ---- dispozitiv ----
    def info(self):
        r = self._apel(self.dev, "<tds:GetDeviceInformation/>")
        return {"manufacturer": text(r, "Manufacturer"), "model": text(r, "Model"),
                "firmware": text(r, "FirmwareVersion"), "serial": text(r, "SerialNumber"),
                "hardware": text(r, "HardwareId")}

    def servicii(self):
        """{media: url, ptz: url, deviceio: url, events: url} — doar cele prezente."""
        if self._xaddr:
            return self._xaddr
        out = {}
        try:
            r = self._apel(self.dev, "<tds:GetServices><tds:IncludeCapability>false</tds:IncludeCapability></tds:GetServices>")
            for s in toate(r, "Service"):
                ns, x = text(s, "Namespace", ""), text(s, "XAddr", "")
                for cheie, frag in (("media", "/media/"), ("ptz", "/ptz/"), ("deviceio", "/deviceIO/"),
                                    ("events", "/events/"), ("media2", "/media/ver20")):
                    if frag in ns and x:
                        out.setdefault(cheie, x)
        except EroareOnvif:
            pass
        if "media" not in out:
            r = self._apel(self.dev, "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>")
            for cheie, tag in (("media", "Media"), ("ptz", "PTZ"), ("deviceio", "DeviceIO"), ("events", "Events")):
                el = gaseste(r, tag)
                x = text(el, "XAddr") if el is not None else None
                if x:
                    out[cheie] = x
        # unele camere raporteaza XAddr cu alt IP/port (NAT, 0.0.0.0): le ancoram pe al nostru
        for k, v in list(out.items()):
            out[k] = re.sub(r"^https?://[^/]+", f"http://{self.ip}:{self.port}", v)
        self._xaddr = out
        return out

    # ---- media ----
    def profiluri(self):
        """[{token, nume, w, h, codec}] in ordinea raportata (de obicei main, sub)."""
        media = self.servicii().get("media")
        if not media:
            raise EroareOnvif(t("camera nu expune serviciul Media"))
        r = self._apel(media, "<trt:GetProfiles/>")
        out = []
        for p in toate(r, "Profiles"):
            vec = gaseste(p, "VideoEncoderConfiguration")
            rez = gaseste(vec, "Resolution") if vec is not None else None
            out.append({"token": p.get("token"), "nume": text(p, "Name", ""),
                        "w": int(text(rez, "Width", 0) or 0) if rez is not None else 0,
                        "h": int(text(rez, "Height", 0) or 0) if rez is not None else 0,
                        "codec": (text(vec, "Encoding", "") if vec is not None else "").upper()})
        if not out:
            raise EroareOnvif(t("camera nu are profiluri media"))
        return out

    def url_flux(self, token):
        media = self.servicii()["media"]
        r = self._apel(media, f'<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>'
                              f'<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>'
                              f'<trt:ProfileToken>{_esc(token)}</trt:ProfileToken></trt:GetStreamUri>')
        u = text(r, "Uri")
        if not u:
            raise EroareOnvif(t("GetStreamUri fără Uri"))
        return u

    def url_poza(self, token):
        media = self.servicii()["media"]
        r = self._apel(media, f"<trt:GetSnapshotUri><trt:ProfileToken>{_esc(token)}</trt:ProfileToken></trt:GetSnapshotUri>")
        return text(r, "Uri")

    def fluxuri(self):
        """{main: rtsp, sub: rtsp|None, poza: url|None, profiluri: [...]}: main = profilul cu
        rezolutia cea mai mare, sub = urmatorul (sau None)."""
        pr = sorted(self.profiluri(), key=lambda p: p["w"] * p["h"], reverse=True)
        main = self.url_flux(pr[0]["token"])
        sub = self.url_flux(pr[1]["token"]) if len(pr) > 1 else None
        poza = None
        try:
            poza = self.url_poza(pr[0]["token"])
        except EroareOnvif:
            pass
        return {"main": main, "sub": sub, "poza": poza, "profiluri": pr}

    def poza(self, url=None):
        """JPEG de la GetSnapshotUri (cu credentialele camerei, Digest sau Basic)."""
        url = url or self.fluxuri().get("poza")
        if not url:
            return None
        run = self.run or requests.get
        for auth in (HTTPDigestAuth(self.user, self.pw), (self.user, self.pw)):
            try:
                r = run(url, auth=auth, timeout=TIMEOUT)
                if r.ok and r.content[:2] == b"\xff\xd8":
                    return r.content
            except requests.RequestException:
                return None
        return None

    # ---- PTZ ----
    def _ptz(self):
        x = self.servicii().get("ptz")
        if not x:
            raise EroareOnvif(t("camera nu are serviciu PTZ"))
        return x

    def _token(self):
        return self.profiluri()[0]["token"]

    def ptz_move(self, pan=0, tilt=0, zoom=0):
        """Miscare continua, fiecare axa -100..100 (0 = stop pe toate)."""
        x = self._ptz()
        tok = _esc(self._token())
        if not (pan or tilt or zoom):
            self._apel(x, f"<tptz:Stop><tptz:ProfileToken>{tok}</tptz:ProfileToken><tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop>")
            return
        n = lambda v: max(-1.0, min(1.0, int(v) / 100.0))
        self._apel(x, f'<tptz:ContinuousMove><tptz:ProfileToken>{tok}</tptz:ProfileToken><tptz:Velocity>'
                      f'<tt:PanTilt x="{n(pan)}" y="{n(tilt)}"/><tt:Zoom x="{n(zoom)}"/></tptz:Velocity></tptz:ContinuousMove>')

    def presetari(self):
        r = self._apel(self._ptz(), f"<tptz:GetPresets><tptz:ProfileToken>{_esc(self._token())}</tptz:ProfileToken></tptz:GetPresets>")
        out = []
        for p in toate(r, "Preset"):
            tok = p.get("token")
            if tok is None:
                continue
            try:
                pid = int(tok)
            except ValueError:
                pid = abs(hash(tok)) % 10000
            out.append({"id": pid, "token": tok, "nume": text(p, "Name", "") or t("Pozitia {id}", id=tok)})
        return out

    def presetare_salveaza(self, nume, token=None):
        tok = _esc(self._token())
        t = f"<tptz:PresetToken>{_esc(token)}</tptz:PresetToken>" if token else ""
        r = self._apel(self._ptz(), f"<tptz:SetPreset><tptz:ProfileToken>{tok}</tptz:ProfileToken><tptz:PresetName>{_esc(nume)}</tptz:PresetName>{t}</tptz:SetPreset>")
        return text(r, "PresetToken")

    def presetare_du_te(self, token):
        self._apel(self._ptz(), f"<tptz:GotoPreset><tptz:ProfileToken>{_esc(self._token())}</tptz:ProfileToken><tptz:PresetToken>{_esc(token)}</tptz:PresetToken></tptz:GotoPreset>")

    def presetare_sterge(self, token):
        self._apel(self._ptz(), f"<tptz:RemovePreset><tptz:ProfileToken>{_esc(self._token())}</tptz:ProfileToken><tptz:PresetToken>{_esc(token)}</tptz:PresetToken></tptz:RemovePreset>")

    # ---- relee ----
    def relee(self):
        x = self.servicii().get("deviceio")
        if not x:
            return []
        try:
            r = self._apel(x, "<tmd:GetRelayOutputs/>")
        except EroareOnvif:
            return []
        return [el.get("token") for el in toate(r, "RelayOutputs") if el.get("token")]

    def releu(self, token, on):
        x = self.servicii().get("deviceio")
        if not x:
            raise EroareOnvif(t("camera nu are serviciu DeviceIO"))
        self._apel(x, f"<tmd:SetRelayOutputState><tmd:RelayOutputToken>{_esc(token)}</tmd:RelayOutputToken>"
                      f"<tmd:LogicalState>{'active' if on else 'inactive'}</tmd:LogicalState></tmd:SetRelayOutputState>")

    # ---- ora ----
    def ora(self):
        r = self._apel(self.dev, "<tds:GetSystemDateAndTime/>")
        utc = gaseste(r, "UTCDateTime")
        if utc is None:
            return None
        d, t = gaseste(utc, "Date"), gaseste(utc, "Time")
        try:
            return datetime.datetime(int(text(d, "Year")), int(text(d, "Month")), int(text(d, "Day")),
                                     int(text(t, "Hour")), int(text(t, "Minute")), int(text(t, "Second")),
                                     tzinfo=datetime.timezone.utc)
        except (TypeError, ValueError):
            return None


def identifica(ip, user, pw, port=80, run=None):
    """Pentru scanner: {vendor: 'onvif', manufacturer, model, ...} sau None daca nu e ONVIF."""
    try:
        c = Camera(ip, user, pw, port, run=run)
        i = c.info()
        return {"vendor": "onvif", "auth": True, "user": user, "pass": pw,
                "model": " ".join(x for x in (i.get("manufacturer"), i.get("model")) if x) or "ONVIF",
                "serial": i.get("serial"), "firmware": i.get("firmware"),
                "name": i.get("model") or "ONVIF"}
    except EroareOnvif as e:
        if str(e) == t("autentificare esuata (user/parola)"):
            return {"vendor": "onvif", "auth": False}
        return None
