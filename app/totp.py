#!/usr/bin/env python3
"""TOTP (RFC 6238) pentru autentificarea in doi pasi — doar biblioteca standard.

Secretul e base32 (160 biti), codul are 6 cifre si se schimba la 30 s, exact ce asteapta
Google Authenticator, Aegis, 1Password etc. `verifica` intoarce CONTORUL pasului potrivit
(nu doar True): apelantul il memoreaza si refuza orice cod cu contor <= ultimul folosit,
ca un cod vazut peste umar sa nu mai mearga a doua oara in aceeasi fereastra.
"""
import base64
import hashlib
import hmac
import os
import secrets
import struct
import time
import urllib.parse
import config as C

PAS = 30          # secunde per cod
CIFRE = 6
FEREASTRA = 1     # +-1 pas tolerat (ceasul telefonului poate fi cu 30 s in urma/inainte)
EMITENT = C.NUME


def secret_nou():
    return base64.b32encode(os.urandom(20)).decode()


def _cheie(secret):
    s = secret.strip().replace(" ", "").upper()
    return base64.b32decode(s + "=" * (-len(s) % 8))


def contor(t=None):
    return int((t if t is not None else time.time()) // PAS)


def cod(secret, t=None, ctr=None):
    """Codul pentru momentul t (sau pentru contorul dat)."""
    c = ctr if ctr is not None else contor(t)
    h = hmac.new(_cheie(secret), struct.pack(">Q", c), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    n = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** CIFRE)
    return f"{n:0{CIFRE}d}"


def verifica(secret, introdus, t=None, dupa=None, fereastra=FEREASTRA):
    """Contorul pasului al carui cod se potriveste, altfel None. `dupa` = ultimul contor
    acceptat: pasii <= dupa sunt refuzati (anti-replay)."""
    introdus = (introdus or "").strip().replace(" ", "")
    if not introdus.isdigit() or len(introdus) != CIFRE:
        return None
    baza = contor(t)
    for d in range(-fereastra, fereastra + 1):
        c = baza + d
        if dupa is not None and c <= dupa:
            continue
        if hmac.compare_digest(cod(secret, ctr=c), introdus):
            return c
    return None


def uri(secret, user, emitent=EMITENT):
    """otpauth://, ce codifica QR-ul pe care il scaneaza aplicatia de autentificare."""
    q = urllib.parse.quote
    return (f"otpauth://totp/{q(emitent)}:{q(user)}?secret={secret}&issuer={q(emitent)}"
            f"&algorithm=SHA1&digits={CIFRE}&period={PAS}")


def qr_svg(text):
    """QR-ul ca SVG (sir), prin `segno`; sir gol daca biblioteca lipseste — interfata
    arata atunci secretul de tastat manual."""
    try:
        import io
        import segno
        buf = io.BytesIO()
        segno.make(text, error="m").save(buf, kind="svg", scale=5, border=2,
                                         dark="#111", light="#fff")
        return buf.getvalue().decode()
    except Exception:
        return ""


def coduri_recuperare(n=8):
    """Coduri de unica folosinta (daca telefonul se pierde): 10 caractere, fara 0/O/1/I."""
    alfabet = "abcdefghjkmnpqrstuvwxyz23456789"
    return ["".join(secrets.choice(alfabet) for _ in range(10)) for _ in range(n)]
