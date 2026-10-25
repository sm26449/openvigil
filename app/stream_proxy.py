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
  - administratorul trece oriunde (inclusiv interfata go2rtc, de la Legaturi);
  - viewer/operator: doar handshake-ul WebSocket pe /api/ws?src=cam|hd|pb<id>, pe
    camerele permise, si doar daca go2rtc raspunde 101 — altfel conexiunea se inchide,
    ca o a doua cerere pe acelasi socket sa nu ajunga nefiltrata la API-ul go2rtc.
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
SRC_RE = re.compile(r"^(?:cam|hd|pb)(\d+)")   # numele fluxurilor: cam1, hd1_h720, pb1_...


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
    def __init__(self, app, current_user, visible_ids, upstream, host, port):
        self.app, self.current_user, self.visible_ids = app, current_user, visible_ids
        self.upstream, self.host, self.port = upstream, host, port

    # ---- decizia ----
    def _user(self, hdr):
        """Sesiunea nvr-web pentru cookie-ul primit: (user, camere vizibile sau None)."""
        with self.app.test_request_context("/", headers={"Cookie": hdr.get("cookie", "")}):
            u = self.current_user()
            return u, (self.visible_ids() if u else None)

    def _allowed(self, method, path, hdr):
        """None daca cererea trece, altfel (cod, mesaj)."""
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
        if u.get("role") == "admin":
            return None
        url = urllib.parse.urlsplit(path)
        if (method != "GET" or url.path != "/api/ws"
                or hdr.get("upgrade", "").lower() != "websocket"):
            return 403, "doar fluxurile video"
        src = urllib.parse.parse_qs(url.query).get("src", [""])[0]
        m = SRC_RE.match(src)
        if not m or (vis is not None and int(m[1]) not in vis):
            return 403, "nu ai acces la aceasta camera"
        return None

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
            deny = self._allowed(method, path, hdr)
            if deny:
                _reply(client, *deny)
                return client.close()
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
            threading.Thread(target=_pump, args=(up, client), daemon=True).start()
            _pump(client, up)
        except (OSError, ValueError) as e:
            if not isinstance(e, (ConnectionResetError, BrokenPipeError, socket.timeout)):
                logare.info(f"[stream_proxy] {str(e)[:80]}")
            self._close(client, up)

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


def start(app, current_user, visible_ids, upstream, host, port):
    """Porneste releul intr-un fir daemon; intoarce obiectul (pentru teste)."""
    p = StreamProxy(app, current_user, visible_ids, upstream, host, port)
    threading.Thread(target=p.serve_forever, daemon=True, name="stream-proxy").start()
    return p
