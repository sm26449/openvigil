#!/usr/bin/env python3
"""Releu TCP autentificat intre browser si go2rtc.

go2rtc asculta doar pe localhost (API :1984, RTSP :8554); pe LAN iese numai acest releu
(:8076, STREAM_PORT din config). Un WebSocket este, dupa handshake, un simplu socket TCP,
asa ca releul nu are nevoie sa inteleaga cadrele: citeste antetele primei cereri HTTP,
decide, apoi copiaza octetii in ambele sensuri.

Decizia foloseste exact sesiunea nvr-web (acelasi cookie, acelasi secret, aceeasi
functie current_user), deci conturile si restrictiile pe camere din interfata se aplica
si fluxurilor video — pana acum go2rtc le ocolea pe toate:
  - fara sesiune -> 401;
  - toti, inclusiv administratorul, ajung DOAR la ce foloseste interfata: handshake-ul
    WebSocket pe /api/ws cu un flux publicat (numele INTREG: cam<id>, hd<id>[_h<treapta>],
    pb<id>_<moment>[_<viteza>][_h<treapta>]) pe camerele permise, iar adminul si la cadrele
    JPEG (/api/frame.jpeg). Fluxurile interne (cam<id>_main = mainstream-ul brut, fara masca)
    si API-ul de administrare go2rtc (/api/config cu parolele camerelor si surse `exec:`,
    /api/restart, /api/streams, /api/log...) raman doar pe localhost (audit S1, S2);
  - cine nu are voie sa vorbeasca prin camera (viewer) primeste, pe camerele cu difuzor,
    copia fara canal de vorbire a fluxului live (cam<id>_v), ca o oferta WebRTC cu audio de
    trimis sa nu ajunga la difuzor (audit S1);
  - handshake-ul trece doar daca go2rtc raspunde 101 — altfel conexiunea se inchide, ca o a
    doua cerere pe acelasi socket sa nu ajunga nefiltrata la API-ul go2rtc;
  - un flux deschis isi reverifica accesul la REVERIFICA secunde si se inchide cand sesiunea
    e revocata, contul sters sau camera retrasa (audit S7).
Origin-ul (cand exista) trebuie sa fie acelasi host ca al cererii: o pagina straina nu
poate deschide fluxuri cu sesiunea utilizatorului.
"""
import re
import socket
import threading
import urllib.parse
import logare

MAX_HDR = 16 * 1024          # antetele unei cereri de browser incap lejer
HDR_TIMEOUT = 15             # secunde pana la antete complete, altfel inchidem
REVERIFICA = 20             # secunde intre reverificarile unui flux deschis
# numele INTREG al fluxurilor publicate browserului (vezi /api/play si 10-live.js)
SRC_RE = re.compile(r"^(?:cam(?P<c>\d+)|hd(?P<h>\d+)(?:_h(?:0|720|1080))?"
                    r"|pb(?P<p>\d+)_\d{8}T\d{6}Z(?:_(?:1|05|2|4|8))?(?:_h(?:0|720|1080))?)$")
ADMIN_GET = ("/api/frame.jpeg",)   # pe langa fluxuri, adminul vede si cadre JPEG
# rezerva HLS (Safari/iOS): dupa ce fluxul s-a deschis prin WebSocket (verificat mai sus), playerul
# cere playlist-ul si segmentele prin GET simplu, cu un `id` aleator al sesiunii HLS create atunci;
# fara id nu se ajunge la niciun flux, deci le lasam oricarui cont autentificat
HLS_RE = re.compile(r"^/api/hls/(?:playlist\.m3u8|segment\.(?:ts|m4s)|init\.mp4)$")
VORBESC = ("admin", "operator")    # rolurile care pot vorbi prin difuzorul camerei


def _hostname(hostport):
    """'nvr:8076' / '[::1]:8076' / 'nvr' -> 'nvr'."""
    try:
        return (urllib.parse.urlsplit("//" + hostport).hostname or "").lower()
    except ValueError:
        return ""


def _read_head(sock):
    """Citeste pana la sfarsitul antetelor. Intoarce (antete, restul deja citit)."""
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk or len(buf) > MAX_HDR:
            return None, b""
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    return head + b"\r\n\r\n", rest


def _parse(head):
    """(metoda, cale, antete-in-litere-mici) sau None daca nu arata a HTTP."""
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return None
    hdr = {}
    for ln in lines[1:]:
        if ":" in ln:
            k, v = ln.split(":", 1)
            hdr[k.strip().lower()] = v.strip()
    return parts[0].upper(), parts[1], hdr


def _reply(sock, code, text):
    body = text.encode()
    sock.sendall(f"HTTP/1.1 {code}\r\nContent-Type: text/plain; charset=utf-8\r\n"
                 f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)


def _pump(src, dst):
    try:
        while True:
            b = src.recv(65536)
            if not b:
                break
            dst.sendall(b)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.close()
            except OSError:
                pass


class StreamProxy:
    def __init__(self, app, current_user, visible_ids, upstream, host, port, are_vorbire=None):
        self.app, self.current_user, self.visible_ids = app, current_user, visible_ids
        self.upstream, self.host, self.port = upstream, host, port
        self.are_vorbire = are_vorbire or (lambda cid: False)   # camera are difuzor (cam<id>_v exista)?

    # ---- decizia ----
    def _user(self, hdr):
        """Sesiunea nvr-web pentru cookie-ul primit: (user, camere vizibile sau None)."""
        with self.app.test_request_context("/", headers={"Cookie": hdr.get("cookie", "")}):
            u = self.current_user()
            return u, (self.visible_ids() if u else None)

    def _allowed(self, method, path, hdr):
        """None daca cererea trece, altfel (cod, mesaj)."""
        r = self._decide(method, path, hdr)
        return r if isinstance(r, tuple) else None

    def _decide(self, method, path, hdr):
        """(cod, mesaj) daca se refuza; altfel calea de trimis la go2rtc (poate fi rescrisa)."""
        origin = hdr.get("origin")
        if origin and _hostname(urllib.parse.urlsplit(origin).netloc) != _hostname(hdr.get("host", "")):
            return 403, "origine straina"
        try:
            u, vis = self._user(hdr)
        except Exception as e:
            logare.info(f"[stream_proxy] sesiune: {str(e)[:80]}")
            return 401, "neautentificat"
        if not u:
            return 401, "neautentificat"
        admin = u.get("role") == "admin"
        url = urllib.parse.urlsplit(path)
        if method != "GET":
            return 403, "doar fluxurile video"
        if admin and url.path in ADMIN_GET:
            return path
        if HLS_RE.match(url.path) and urllib.parse.parse_qs(url.query).get("id"):
            return path
        if url.path != "/api/ws" or hdr.get("upgrade", "").lower() != "websocket":
            return 403, "doar fluxurile video"
        q = urllib.parse.parse_qs(url.query)
        src = q.get("src", [""])[0]
        m = SRC_RE.match(src)
        if not m or len(q.get("src", [])) != 1:
            return 403, "flux necunoscut"
        cid = int(m["c"] or m["h"] or m["p"])
        if vis is not None and cid not in vis:
            return 403, "nu ai acces la aceasta camera"
        if m["c"] and u.get("role") not in VORBESC and self.are_vorbire(cid):
            q["src"] = [f"cam{cid}_v"]                 # live fara canal de vorbire
            return urllib.parse.urlunsplit(url._replace(query=urllib.parse.urlencode(q, doseq=True)))
        return path

    # ---- o conexiune ----
    def _handle(self, client):
        up = None
        try:
            client.settimeout(HDR_TIMEOUT)
            head, rest = _read_head(client)
            req = _parse(head) if head else None
            if not req:
                return client.close()
            method, path, hdr = req
            dec = self._decide(method, path, hdr)
            if isinstance(dec, tuple):
                _reply(client, *dec)
                return client.close()
            if dec != path:                   # sursa rescrisa (ex. live fara canal de vorbire)
                linie = head.split(b"\r\n", 1)
                head = f"{method} {dec} HTTP/1.1".encode("latin-1") + b"\r\n" + linie[1]
            up = socket.create_connection(self.upstream, timeout=10)
            # cookie-ul sesiunii nu are ce cauta in go2rtc
            fwd = b"\r\n".join(ln for ln in head.split(b"\r\n")
                               if not ln.lower().startswith(b"cookie:"))
            if hdr.get("upgrade", "").lower() == "websocket":
                # Trimitem DOAR handshake-ul si asteptam 101 inainte sa deschidem conducta:
                # un 4xx ar lasa socketul in HTTP, iar ce vine dupa antete (o a doua cerere
                # lipita de prima) ar ajunge nefiltrat la API-ul go2rtc.
                up.sendall(fwd)
                up.settimeout(HDR_TIMEOUT)
                resp, tail = _read_head(up)
                if not resp:
                    return self._close(client, up)
                client.sendall(resp + tail)
                if not resp.startswith(b"HTTP/1.1 101"):
                    return self._close(client, up)
                if rest:                      # de-acum sunt cadre WebSocket
                    up.sendall(rest)
            else:                             # HTTP simplu: doar administratorul ajunge aici
                # O singura cerere per conexiune: releul decide DOAR pe prima. Fara
                # `Connection: close`, un proxy din fata (Traefik, pentru HTTPS) ar refolosi
                # conexiunea si ar trimite pe ea cererea ALTUI client, nefiltrata.
                fwd = b"\r\n".join(ln for ln in fwd.split(b"\r\n")
                                   if not ln.lower().startswith(b"connection:"))
                fwd = fwd.replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n", 1)
                up.sendall(fwd + rest)
            for s in (client, up):
                s.settimeout(None)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            gata = threading.Event()
            threading.Thread(target=self._paznic, args=(gata, client, up, method, path, hdr),
                             daemon=True).start()
            threading.Thread(target=_pump, args=(up, client), daemon=True).start()
            _pump(client, up)
            gata.set()
        except (OSError, ValueError) as e:
            if not isinstance(e, (ConnectionResetError, BrokenPipeError, socket.timeout)):
                logare.info(f"[stream_proxy] {str(e)[:80]}")
            self._close(client, up)

    def _paznic(self, gata, client, up, method, path, hdr):
        """Reverifica periodic accesul unui flux deschis: sesiunea revocata, contul sters sau
        camera retrasa inchid conexiunea (altfel fluxul curgea pana il inchidea browserul)."""
        while not gata.wait(REVERIFICA):
            try:
                refuz = self._allowed(method, path, hdr)
            except Exception:
                refuz = (500, "eroare")
            if refuz:
                logare.info(f"[stream_proxy] flux inchis: {refuz[1]}")
                self._close(client, up)
                return

    @staticmethod
    def _close(*socks):
        for s in socks:
            if s:
                try:
                    s.close()
                except OSError:
                    pass

    # ---- bucla de acceptare ----
    def _listen(self):
        # host "0.0.0.0" -> dual-stack (ca ":1984" al go2rtc); daca IPv6 lipseste, doar v4
        if self.host in ("", "0.0.0.0", "::"):
            try:
                s = socket.socket(socket.AF_INET6)
                s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("::", self.port))
                return s
            except OSError:
                pass
        s = socket.socket(socket.AF_INET)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host or "0.0.0.0", self.port))
        return s

    def serve_forever(self):
        srv = self._listen()
        srv.listen(64)
        logare.info(f"[stream_proxy] :{self.port} -> {self.upstream[0]}:{self.upstream[1]}")
        while True:
            try:
                c, _ = srv.accept()
            except OSError as e:
                logare.info(f"[stream_proxy] accept: {e}")
                continue
            threading.Thread(target=self._handle, args=(c,), daemon=True).start()


def start(app, current_user, visible_ids, upstream, host, port, are_vorbire=None):
    """Porneste releul intr-un fir daemon; intoarce obiectul (pentru teste)."""
    p = StreamProxy(app, current_user, visible_ids, upstream, host, port, are_vorbire)
    threading.Thread(target=p.serve_forever, daemon=True, name="stream-proxy").start()
    return p
