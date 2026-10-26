#!/usr/bin/env python3
"""sd_notify fara biblioteci: READY=1 la pornire si WATCHDOG=1 periodic, catre systemd.

Cu Type=notify + WatchdogSec in unitate, systemd repornste serviciul daca nu mai primeste
bataia de inima — un proces viu dar intepenit (fir mort, deadlock) nu mai sta ore in sir
„activ". Fara NOTIFY_SOCKET (Docker, rulat de mana) bataia de inima atinge fisierul
HEARTBEAT (healthcheck-ul din Compose il citeste), iar start_watchdog() opreste procesul dupa
BOLNAV_MAX verificari esuate la rand: `restart: unless-stopped` il porneste din nou, pentru ca
Docker nu reporneste singur un container doar „unhealthy"."""
import os
import socket
import threading
import time
import logare

HEARTBEAT = os.environ.get("OPENVIGIL_HEARTBEAT")   # ex. data/.heartbeat-record
BOLNAV_MAX = 4


def notify(msg):
    cale = os.environ.get("NOTIFY_SOCKET")
    if not cale:
        return False
    if cale.startswith("@"):
        cale = "\0" + cale[1:]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as s:
            s.connect(cale)
            s.sendall(msg.encode())
        return True
    except OSError:
        return False


def ready():
    return notify("READY=1")


def watchdog():
    if HEARTBEAT:
        try:
            with open(HEARTBEAT, "a"):
                pass
            os.utime(HEARTBEAT, None)
        except OSError:
            pass
    return notify("WATCHDOG=1")


def start_watchdog(sanatos, interval=30):
    """Fir care trimite WATCHDOG=1 la `interval` s DOAR daca `sanatos()` e True — altfel
    tace si systemd reporneste serviciul dupa WatchdogSec. Fara systemd, dupa BOLNAV_MAX
    esecuri la rand procesul iese cu cod 1 si supervizorul (Docker) il reporneste."""
    def bucla():
        bolnav = 0
        while True:
            try:
                ok = bool(sanatos())
            except Exception as e:
                logare.info(f"[watchdog] {str(e)[:60]}")
                ok = False
            if ok:
                bolnav = 0
                watchdog()
            else:
                bolnav += 1
                if bolnav >= BOLNAV_MAX and not os.environ.get("NOTIFY_SOCKET"):
                    logare.error(f"[watchdog] bolnav de {bolnav} ori la rand, ies ca sa fiu repornit")
                    os._exit(1)
            time.sleep(interval)
    threading.Thread(target=bucla, daemon=True, name="sd-watchdog").start()
