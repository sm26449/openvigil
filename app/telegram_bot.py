#!/usr/bin/env python3
"""Comenzi prin Telegram: /acasa, /plecat, /noapte, /mod, /stare — de pe telefon, fara
interfata. Notificarile pleaca oricum prin acelasi bot (notify.py); aici citim ce
raspunde omul, cu long-polling (getUpdates), intr-un fir al nvr-web.

Securitate: se asculta DOAR chat-ul configurat in Setari (telegram_chat) — orice alt
chat primeste un refuz scurt si e jurnalizat. Pornit explicit din Setari
(`telegram_comenzi`), ca un al doilea proces cu acelasi bot sa nu se bata pe getUpdates.
"""
import threading
import time

import requests

import settings
import logare

_fir = None
_offset = 0
_oprit = threading.Event()
COMENZI = {"/acasa": "acasa", "/plecat": "plecat", "/noapte": "noapte"}


def activ():
    return bool(settings.get("telegram_comenzi") and settings.get("telegram_token")
                and settings.get("telegram_chat"))


def _trimite(cfg, text):
    requests.post(f"https://api.telegram.org/bot{cfg['telegram_token']}/sendMessage",
                  data={"chat_id": cfg["telegram_chat"], "text": text}, timeout=15)


def _stare_text():
    import mod
    import continuity
    m = mod.get()
    try:
        live = continuity.live()
    except Exception:
        live = []
    jos = [c["name"] for c in live if not c.get("scrie")]
    txt = f"{mod.MOD_ICO[m['mod']]} Mod: {mod.MOD_RO[m['mod']]}"
    if m.get("de_cand"):
        txt += f" (din {time.strftime('%H:%M', time.localtime(m['de_cand']))}, {m.get('sursa') or '?'})"
    txt += f"\n📹 {len(live)} camere inregistreaza" + (f"; NU scriu: {', '.join(jos)}" if jos else "")
    return txt


def raspuns(text, cine="telegram"):
    """Textul de raspuns la o comanda (pur, testabil); None daca nu e o comanda a noastra."""
    import mod
    cmd = (text or "").strip().split()[0].lower() if (text or "").strip() else ""
    cmd = cmd.split("@")[0]
    if cmd in COMENZI:
        m = COMENZI[cmd]
        era = mod.curent()
        mod.set(m, cine=cine, sursa="telegram")
        if era == m:
            return f"{mod.MOD_ICO[m]} Deja in modul {mod.MOD_RO[m]}."
        return f"{mod.MOD_ICO[m]} Mod: {mod.MOD_RO[m]} (era {mod.MOD_RO[era]})."
    if cmd in ("/mod", "/stare", "/status", "/start", "/help"):
        return (_stare_text() + "\n\nComenzi: /acasa, /plecat, /noapte, /stare")
    return None


def _bucla():
    global _offset
    while not _oprit.is_set():
        cfg = settings.load()
        if not activ():
            time.sleep(30)
            continue
        try:
            r = requests.get(f"https://api.telegram.org/bot{cfg['telegram_token']}/getUpdates",
                             params={"offset": _offset, "timeout": 25,
                                     "allowed_updates": '["message"]'}, timeout=35)
            d = r.json()
            if not d.get("ok"):
                logare.info(f"[telegram] getUpdates: {str(d.get('description'))[:80]}")
                time.sleep(30)
                continue
            for u in d.get("result") or []:
                _offset = max(_offset, int(u["update_id"]) + 1)
                msg = u.get("message") or {}
                chat = str((msg.get("chat") or {}).get("id") or "")
                text = msg.get("text") or ""
                if chat != str(cfg["telegram_chat"]):
                    import audit
                    audit.log("comanda telegram refuzata (chat străin)", chat, {"text": text[:40]})
                    continue
                if time.time() - int(msg.get("date") or 0) > 300:
                    continue          # comanda veche (serverul a fost oprit): nu o executam
                cine = (msg.get("from") or {}).get("first_name") or "telegram"
                try:
                    rasp = raspuns(text, cine=f"telegram:{cine}")
                except Exception as e:
                    rasp = f"Eroare: {str(e)[:80]}"
                if rasp:
                    _trimite(cfg, rasp)
        except Exception as e:
            logare.info(f"[telegram] {str(e)[:80]}")
            time.sleep(15)


def start():
    global _fir
    if _fir is None:
        _fir = threading.Thread(target=_bucla, daemon=True, name="telegram-bot")
        _fir.start()
