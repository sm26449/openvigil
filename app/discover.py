#!/usr/bin/env python3
"""Descoperire camere in retea (scanner).

Doua etape: (1) scanare rapida de porturi pe intervalul dat (80/8000/554 deschise),
(2) identificare prin ISAPI cu credentialele date -> model, serial, MAC, firmware.
Camerele deja adaugate sunt marcate ca atare. Non-Hikvision se identifica prin ONVIF
(GetDeviceInformation cu aceleasi credentiale) si se adauga cu vendor "onvif".
"""
import concurrent.futures as cf
import ipaddress
import socket
import threading
import xml.etree.ElementTree as ET

import requests
from requests.auth import HTTPDigestAuth

PORTS = (80, 8000, 554)
state = {"running": False, "done": 0, "total": 0, "found": [], "error": None}
_lock = threading.Lock()


def _port_open(ip, port, timeout=0.35):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        return s.connect_ex((str(ip), port)) == 0


def _identify(ip, users):
    """Incearca ISAPI cu fiecare pereche user/parola; intoarce dict sau None."""
    for user, pw in users:
        try:
            s = requests.Session()
            s.auth = HTTPDigestAuth(user, pw)
            r = s.get(f"http://{ip}/ISAPI/System/deviceInfo", timeout=6)
            if r.status_code == 401:
                continue
            if not r.ok or "<DeviceInfo" not in r.text:
                # nu e Hikvision: incercam ONVIF cu aceleasi credentiale
                import onvif
                o = onvif.identifica(str(ip), user, pw)
                if o:
                    return {"ip": str(ip), **o}
                return {"ip": str(ip), "vendor": "necunoscut", "auth": False}
            root = ET.fromstring(r.text)
            g = lambda t: next((e.text for e in root.iter() if e.tag.endswith(t)), None)
            return {"ip": str(ip), "vendor": "hikvision", "auth": True,
                    "user": user, "pass": pw,
                    "model": g("model"), "serial": g("serialNumber"),
                    "mac": g("macAddress"), "firmware": g("firmwareVersion"),
                    "name": g("deviceName") or g("model")}
        except requests.RequestException:
            continue
    # niciun ISAPI: poate e ONVIF cu una dintre parole
    import onvif
    for user, pw in users:
        o = onvif.identifica(str(ip), user, pw)
        if o and o.get("auth"):
            return {"ip": str(ip), **o}
    return {"ip": str(ip), "vendor": "hikvision?", "auth": False}


def scan(cidr, users, known_ips):
    """Scaneaza un interval (ex. 192.168.1.0/24) si populeaza `state`."""
    try:
        net = ipaddress.ip_network(cidr, strict=False)
    except ValueError as e:
        with _lock:
            state.update(running=False, error=f"interval invalid: {e}")
        return
    hosts = list(net.hosts())[:1024]
    with _lock:
        state.update(running=True, done=0, total=len(hosts), found=[], error=None)

    def probe(ip):
        try:
            if not any(_port_open(ip, p) for p in PORTS):
                return None
            info = _identify(ip, users)
            info["known"] = str(ip) in known_ips
            return info
        finally:
            with _lock:
                state["done"] += 1

    try:
        with cf.ThreadPoolExecutor(max_workers=64) as ex:
            for info in ex.map(probe, hosts):
                if info:
                    with _lock:
                        state["found"].append(info)
    except Exception as e:
        with _lock:
            state["error"] = str(e)
    finally:
        with _lock:
            state["found"].sort(key=lambda x: tuple(int(p) for p in x["ip"].split(".")))
            state["running"] = False


def start(cidr, users, known_ips):
    if state["running"]:
        return False
    threading.Thread(target=scan, args=(cidr, users, known_ips), daemon=True).start()
    return True
