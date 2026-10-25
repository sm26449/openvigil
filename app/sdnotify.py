#!/usr/bin/env python3
"""sd_notify fara biblioteci: READY=1 la pornire si WATCHDOG=1 periodic, catre systemd.

Cu Type=notify + WatchdogSec in unitate, systemd repornste serviciul daca nu mai primeste
bataia de inima — un proces viu dar intepenit (fir mort, deadlock) nu mai sta ore in sir
„activ". Fara NOTIFY_SOCKET (rulat de mana) totul e no-op."""
import os
import socket
import threading
import logare


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
    return notify("WATCHDOG=1")


def start_watchdog(sanatos, interval=30):
    """Fir care trimite WATCHDOG=1 la `interval` s DOAR daca `sanatos()` e True — altfel
    tace si systemd reporneste serviciul dupa WatchdogSec."""
    import time

    def bucla():
        while True:
            try:
                if sanatos():
                    watchdog()
            except Exception as e:
                logare.info(f"[watchdog] {str(e)[:60]}")
            time.sleep(interval)
    threading.Thread(target=bucla, daemon=True, name="sd-watchdog").start()
