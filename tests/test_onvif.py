"""Clientul ONVIF (app/onvif.py) pe un server SOAP fals: identificare, servicii, profiluri,
URL-uri de flux, poza, PTZ, presetari, relee, scanner."""
import http.server
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import drivers  # noqa: E402
import onvif  # noqa: E402

PRIMITE = []
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 10


class Fals(http.server.BaseHTTPRequestHandler):
    """Raspunde la apelurile SOAP dupa numele operatiei din corp; port-ul e in server."""
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/snap"):
            if "Authorization" not in self.headers:
                self.send_response(401); self.send_header("WWW-Authenticate", 'Digest realm="x", nonce="abc"'); self.end_headers(); return
            self.send_response(200); self.send_header("Content-Type", "image/jpeg"); self.end_headers(); self.wfile.write(JPEG); return
        self.send_response(404); self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        corp = self.rfile.read(n).decode()
        PRIMITE.append((self.path, corp))
        base = f"http://{self.headers['Host']}"
        if "<wsse:Security" not in corp and "Authorization" not in self.headers:
            self.send_response(401); self.end_headers(); return
        if "<wsse:Username>rau</wsse:Username>" in corp:
            r = '<s:Fault><s:Code><s:Value>s:Sender</s:Value><s:Subcode><s:Value>ter:NotAuthorized</s:Value></s:Subcode></s:Code><s:Reason><s:Text>Sender not Authorized</s:Text></s:Reason></s:Fault>'
            self._raspunde(r, 400); return
        if "GetDeviceInformation" in corp:
            r = "<tds:GetDeviceInformationResponse><tds:Manufacturer>Dahua</tds:Manufacturer><tds:Model>IPC-X</tds:Model><tds:FirmwareVersion>2.8</tds:FirmwareVersion><tds:SerialNumber>SN1</tds:SerialNumber></tds:GetDeviceInformationResponse>"
        elif "GetServices" in corp:
            r = (f"<tds:GetServicesResponse><tds:Service><tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace><tds:XAddr>http://0.0.0.0:9999/onvif/media</tds:XAddr></tds:Service>"
                 f"<tds:Service><tds:Namespace>http://www.onvif.org/ver20/ptz/wsdl</tds:Namespace><tds:XAddr>{base}/onvif/ptz</tds:XAddr></tds:Service>"
                 f"<tds:Service><tds:Namespace>http://www.onvif.org/ver10/deviceIO/wsdl</tds:Namespace><tds:XAddr>{base}/onvif/deviceIO</tds:XAddr></tds:Service></tds:GetServicesResponse>")
        elif "GetProfiles" in corp:
            r = ('<trt:GetProfilesResponse><trt:Profiles token="sub"><tt:Name>Sub</tt:Name><tt:VideoEncoderConfiguration><tt:Encoding>H264</tt:Encoding><tt:Resolution><tt:Width>640</tt:Width><tt:Height>480</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles>'
                 '<trt:Profiles token="main"><tt:Name>Main</tt:Name><tt:VideoEncoderConfiguration><tt:Encoding>H265</tt:Encoding><tt:Resolution><tt:Width>2560</tt:Width><tt:Height>1440</tt:Height></tt:Resolution></tt:VideoEncoderConfiguration></trt:Profiles></trt:GetProfilesResponse>')
        elif "GetStreamUri" in corp:
            tok = corp.split("<trt:ProfileToken>")[1].split("<")[0]
            r = f"<trt:GetStreamUriResponse><trt:MediaUri><tt:Uri>rtsp://10.0.0.9:554/{tok}</tt:Uri></trt:MediaUri></trt:GetStreamUriResponse>"
        elif "GetSnapshotUri" in corp:
            r = f"<trt:GetSnapshotUriResponse><trt:MediaUri><tt:Uri>{base}/snap.jpg</tt:Uri></trt:MediaUri></trt:GetSnapshotUriResponse>"
        elif "GetPresets" in corp:
            r = '<tptz:GetPresetsResponse><tptz:Preset token="1"><tt:Name>Poarta</tt:Name></tptz:Preset><tptz:Preset token="7"/></tptz:GetPresetsResponse>'
        elif "SetPreset" in corp:
            r = "<tptz:SetPresetResponse><tptz:PresetToken>9</tptz:PresetToken></tptz:SetPresetResponse>"
        elif "GetRelayOutputs" in corp:
            r = '<tmd:GetRelayOutputsResponse><tmd:RelayOutputs token="r1"/><tmd:RelayOutputs token="r2"/></tmd:GetRelayOutputsResponse>'
        elif "GetSystemDateAndTime" in corp:
            r = "<tds:GetSystemDateAndTimeResponse><tds:SystemDateAndTime><tt:UTCDateTime><tt:Time><tt:Hour>10</tt:Hour><tt:Minute>20</tt:Minute><tt:Second>30</tt:Second></tt:Time><tt:Date><tt:Year>2026</tt:Year><tt:Month>10</tt:Month><tt:Day>23</tt:Day></tt:Date></tt:UTCDateTime></tds:SystemDateAndTime></tds:GetSystemDateAndTimeResponse>"
        else:
            r = f"<{corp.split('<s:Body>')[1].split('>')[0][1:].split('/')[0].split(' ')[0]}Response/>"
        self._raspunde(r)

    def _raspunde(self, inner, cod=200):
        env = (f'<s:Envelope xmlns:s="{onvif.NS["s"]}" xmlns:tds="{onvif.NS["tds"]}" xmlns:trt="{onvif.NS["trt"]}" '
               f'xmlns:tptz="{onvif.NS["tptz"]}" xmlns:tmd="{onvif.NS["tmd"]}" xmlns:tt="{onvif.NS["tt"]}"><s:Body>{inner}</s:Body></s:Envelope>').encode()
        self.send_response(cod); self.send_header("Content-Type", "application/soap+xml"); self.send_header("Content-Length", str(len(env))); self.end_headers(); self.wfile.write(env)


@pytest.fixture(scope="module")
def server():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fals)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def test_identificare_si_servicii(server):
    c = onvif.Camera("127.0.0.1", "u", "p", server)
    i = c.info()
    assert i["manufacturer"] == "Dahua" and i["model"] == "IPC-X" and i["serial"] == "SN1"
    s = c.servicii()
    # XAddr cu 0.0.0.0:9999 e re-ancorat pe adresa/portul nostru
    assert s["media"] == f"http://127.0.0.1:{server}/onvif/media" and "ptz" in s and "deviceio" in s
    assert "<wsse:Security" in PRIMITE[0][1] and "PasswordDigest" in PRIMITE[0][1]
    with pytest.raises(onvif.EroareOnvif, match="autentificare"):
        onvif.Camera("127.0.0.1", "rau", "p", server).info()
    with pytest.raises(onvif.EroareOnvif, match="nu r"):
        onvif.Camera("127.0.0.1", "u", "p", 1).info()


def test_fluxuri_poza_ptz_presetari_relee(server):
    c = onvif.Camera("127.0.0.1", "u", "p", server)
    fl = c.fluxuri()
    assert fl["main"] == "rtsp://10.0.0.9:554/main" and fl["sub"] == "rtsp://10.0.0.9:554/sub"   # cel mai mare = main
    assert fl["poza"].endswith("/snap.jpg") and fl["profiluri"][0]["codec"] == "H265"
    assert c.poza(fl["poza"]) == JPEG                                   # Digest dupa 401
    c.ptz_move(50, -20, 0)
    assert 'x="0.5"' in PRIMITE[-1][1] and 'y="-0.2"' in PRIMITE[-1][1] and PRIMITE[-1][0] == "/onvif/ptz"
    c.ptz_move()
    assert "<tptz:Stop>" in PRIMITE[-1][1]
    assert c.presetari() == [{"id": 1, "token": "1", "nume": "Poarta"}, {"id": 7, "token": "7", "nume": "Pozitia 7"}]
    assert c.presetare_salveaza("Nou") == "9"
    c.presetare_du_te("1"); assert "<tptz:PresetToken>1</tptz:PresetToken>" in PRIMITE[-1][1]
    assert c.relee() == ["r1", "r2"]
    c.releu("r2", True); assert "<tmd:LogicalState>active</tmd:LogicalState>" in PRIMITE[-1][1]
    assert c.ora().isoformat() == "2026-10-23T10:20:30+00:00"


def test_prin_drivers_si_scanner(server):
    cam = {"id": 101, "ip": "127.0.0.1", "user": "u", "pass": "p", "vendor": "onvif", "onvif": {"port": server}}
    fl = drivers.descopera_fluxuri(cam)
    assert fl["main"].endswith("/main") and fl["port"] == server
    cam["onvif"].update(fl)
    assert drivers.rtsp_main(cam) == "rtsp://u:p@10.0.0.9:554/main"
    assert drivers.snapshot(cam) == JPEG and drivers.ping(cam) is True
    f = drivers.probe_features(cam)
    assert f["io_out"] == 2 and f["zoom"] is True and f["siren"] is False and f["presets"] is True
    assert drivers.device_info(cam)["name"] == "IPC-X"
    drivers.set_output(cam, 2, False); assert "<tmd:RelayOutputToken>r2" in PRIMITE[-1][1] and "inactive" in PRIMITE[-1][1]
    drivers.preset_goto(cam, 7); assert "<tptz:PresetToken>7" in PRIMITE[-1][1]
    assert drivers.get_time(cam)["tz"] == "UTC"
    o = onvif.identifica("127.0.0.1", "u", "p", server)
    assert o["vendor"] == "onvif" and o["auth"] and o["model"] == "Dahua IPC-X"
    assert onvif.identifica("127.0.0.1", "rau", "p", server) == {"vendor": "onvif", "auth": False}
    assert onvif.identifica("127.0.0.1", "u", "p", 1) is None
