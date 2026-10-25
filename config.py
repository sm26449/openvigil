"""Configurarea OpenVigil, citita din variabile de mediu.

Docker Compose le ia din `.env` (vezi `.env.example`); pe o instalare clasica le exporti
in unitatea systemd sau in shell. Nicio valoare secreta nu sta in acest fisier: el e in git.
Tot ce se poate schimba din interfata (Setari) sta in `data/settings.json`, nu aici.
"""
import os


def _text(cheie, implicit=""):
    return os.environ.get(cheie, implicit).strip()


def _int(cheie, implicit):
    try:
        return int(_text(cheie) or implicit)
    except ValueError:
        return implicit


def _bool(cheie, implicit=False):
    v = _text(cheie).lower()
    if not v:
        return implicit
    return v in ("1", "true", "da", "yes", "on")


def _lista(cheie, implicit):
    v = _text(cheie)
    return [x.strip() for x in v.split(",") if x.strip()] if v else list(implicit)


# numele sistemului: titlul interfetei, emitentul 2FA, prefixul notificarilor
NUME = _text("NVR_NAME", "OpenVigil")

# interfata web (nvr-web). 0.0.0.0 = toate interfetele gazdei.
WEB_HOST = _text("WEB_HOST", "0.0.0.0")
WEB_PORT = _int("WEB_PORT", 8079)
# releul autentificat spre go2rtc (fluxurile video ajung in browser doar prin el)
STREAM_PORT = _int("STREAM_PORT", 8076)
# serviciul de inferenta (nvr-ai), doar pe localhost
AI_PORT = _int("AI_PORT", 8077)

# go2rtc (motorul de streaming): API si RTSP, doar pe localhost
GO2RTC_API = _text("GO2RTC_API", "http://127.0.0.1:1984")
GO2RTC_RTSP = _text("GO2RTC_RTSP", "rtsp://127.0.0.1:8554")
# WebRTC (media direct browser <-> go2rtc, pe toate interfetele; semnalizarea e autentificata)
WEBRTC_PORT = _int("WEBRTC_PORT", 8555)

# in spatele unui reverse proxy cu TLS (Traefik, Caddy, nginx): cookie Secure + adresa
# clientului din X-Forwarded-For, dar numai de la proxy-urile de incredere
HTTPS = _bool("HTTPS", False)
COOKIE_DOMAIN = _text("COOKIE_DOMAIN") or None
TRUSTED_PROXIES = _lista("TRUSTED_PROXIES", ("127.0.0.0/8", "::1/128", "172.16.0.0/12"))

# inaltimea fluxului HD/playback transcodat pentru browser; 0 = rezolutie nativa (recomandat
# cu GPU). O valoare (ex. 1440) limiteaza banda pe legaturi lente, dar muta decodarea pe CPU.
PLAYBACK_HEIGHT = _int("PLAYBACK_HEIGHT", 0)

# optional: un NVR Hikvision existent, ca sursa de rezerva si istoric vechi. Gol = fara NVR.
NVR_IP = _text("NVR_IP")
NVR_PORT = _int("NVR_PORT", 554)
NVR_USER = _text("NVR_USER", "admin")
NVR_PASS = _text("NVR_PASS")

# doar valori de pornire; se seteaza din Setari -> Notificari (data/settings.json)
TELEGRAM_TOKEN = _text("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _text("TELEGRAM_CHAT_ID")
NOTIFY_LABELS = _lista("NOTIFY_LABELS", ("person", "dog", "cat", "bear"))
NOTIFY_COOLDOWN = _int("NOTIFY_COOLDOWN", 300)

# inregistrarea locala: retentie si pragul de spatiu liber sub care se sterg cele mai vechi
RECORD_KEEP_DAYS = _int("RECORD_KEEP_DAYS", 14)
RECORD_MIN_FREE_GB = _int("RECORD_MIN_FREE_GB", 50)
