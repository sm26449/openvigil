#!/usr/bin/env python3
"""Iesirea catre automatizari: fiecare eveniment pleaca ca POST JSON catre Node-RED.

Ideea nu e sa mai construim noi un motor de reguli, ci sa dam afara evenimentele intr-o
forma stabila si sa lasam Node-RED sa lege ce vrea: aprinde lumina cand se detecteaza
miscare in zona din spate, saluta cand vede un om la poarta, anunta pe alt canal cand
apare un animal.

Reguli de forma pe care ne bazam, ca fluxurile sa nu se strice la fiecare versiune:
- numele evenimentului si cheile din payload sunt stabile; adaugam campuri, nu le
  redenumim;
- fiecare livrare are un id unic, ca partea cealalta sa poata ignora duplicatele daca
  reincercam;
- daca e configurat un secret, semnam corpul cu HMAC-SHA256 in antetul X-Semnatura, ca
  Node-RED sa poata verifica de la cine vine.

Livrarea se face pe un fir separat, cu trei incercari si pauze crescatoare. Un Node-RED
oprit nu trebuie sa incetineasca detectia sau inregistrarea, deci nu asteptam niciodata
dupa el.
"""
import hashlib
import hmac
import json
import threading
import time
import uuid

import requests

import settings
import logare
from i18n import t

# Evenimentele pe care le putem trimite. Cheia e ce apare in payload si in setari;
# textul e doar pentru interfata.
EVENIMENTE = {
    "detectie": "Obiect detectat de inteligența artificială (om, câine, pisică…)",
    "alarma_camera": "Alarmă raportată de cameră (mișcare, linie trecută, intrare în zonă)",
    "zgomot": "Prag de zgomot depășit",
    "scena_candidat": "Ceva nou in scena unei camere (detectie pe schimbare), cu eticheta propusa",
    "scena_analiza": "Analiza adanca a unui candidat „ceva nou” (YOLO + vocabular + VLM), la cerere",
    "camera_offline": "O cameră nu mai răspunde",
    "camera_online": "O cameră a revenit",
    "gol_inregistrare": "Gol în înregistrarea unei camere",
    "sirena": "S-a declansat sirena",
    "persoana": "Persoana recunoscuta (Face ID), cu numele ei",
    "persoana_necunoscuta": "Față găsită, dar care nu e în bibliotecă",
    "fata_falsa": "Tentativă de păcălire: poză sau ecran arătat camerei",
    "descriere": "Semnalmentele persoanei detectate (gen, vârstă, îmbrăcăminte)",
    "obiect": "Obiect urmărit complet (ByteTrack): etichetă, durată, direcție",
    "trecere": "Obiect care a traversat o linie de numărare (sens: intră/iese)",
    "intrare_zona": "Obiect care a intrat într-o zonă definită",
    "intrus": "Intrus CONFIRMAT de AI la perimetru (om/vehicul pe cadrul HD)",
    "obiect_nou": "Obiect nou apărut și rămas în cadru (cameră statică)",
    "obiect_disparut": "Obiect care era în cadru și a dispărut",
    "numar": "Număr de înmatriculare citit (LPR), cu numele mașinii dacă e cunoscută",
    "regula": "O regulă din motorul de reguli s-a declanșat",
    "parcare_inceput": "Un vehicul a parcat (a stat pe loc 2+ minute)",
    "parcare_sfarsit": "Vehiculul parcat a plecat (cu durata staționării)",
    "episod": "Episod de prezență umană încheiat: cine, cât, pe unde, încotro",
}

TIMEOUT = 6
INCERCARI = 3

_jurnal = []          # ultimele livrari, pentru interfata
_lock = threading.Lock()


def _cam_info(cam_id):
    if cam_id is None:
        return None
    try:
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "data", "cameras.json")) as f:
            for c in json.load(f):
                if c["id"] == cam_id:
                    return {"id": c["id"], "nume": c["name"], "grup": c.get("group"),
                            "ip": c["ip"]}
    except (OSError, ValueError):
        pass
    return {"id": cam_id}


def _note(url, ok, detaliu):
    with _lock:
        _jurnal.insert(0, {"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "url": url,
                           "ok": ok, "detaliu": detaliu})
        del _jurnal[50:]


def jurnal():
    with _lock:
        return list(_jurnal)


def _trimite(url, secret, corp):
    date = json.dumps(corp, ensure_ascii=False).encode()
    antete = {"Content-Type": "application/json; charset=utf-8",
              "X-Eveniment": corp["eveniment"], "X-Id": corp["id"]}
    if secret:
        antete["X-Semnatura"] = "sha256=" + hmac.new(
            secret.encode(), date, hashlib.sha256).hexdigest()
    for i in range(INCERCARI):
        try:
            r = requests.post(url, data=date, headers=antete, timeout=TIMEOUT)
            if r.ok:
                _note(url, True, f"HTTP {r.status_code}")
                return True
            ultim = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            ultim = type(e).__name__
        if i < INCERCARI - 1:
            time.sleep(2 ** i)      # 1s, apoi 2s
    _note(url, False, t("{ultim} după {n} încercări", ultim=ultim, n=INCERCARI))
    return False


def emit(eveniment, cam_id=None, **date):
    """Trimite un eveniment catre toate adresele configurate. Nu blocheaza niciodata
    apelantul si NU ARUNCA niciodata: apelantii (detectie, Face ID, LPR...) infasoara
    apelul in `except: pass`, deci o exceptie de aici ar omori automatizarile fara
    nicio urma in jurnal (audit Lot 2).

    Tot pe aici trec evenimentele si catre jurnalul permanent + motorul de reguli —
    INAINTE de verificarea adreselor: regulile functioneaza si fara niciun webhook."""
    try:
        _emit(eveniment, cam_id, date)
    except Exception as e:
        logare.error(f"[hooks] emit '{eveniment}' esuat: {str(e)[:80]}")


def _emit(eveniment, cam_id, date):
    try:
        import rules
        rules.on_event(eveniment, cam_id, date)
    except Exception as e:
        logare.info(f"[hooks] reguli: {str(e)[:80]}")
    try:
        import mqtt
        mqtt.publish_event(eveniment, cam_id, date)
    except Exception as e:
        logare.info(f"[hooks] mqtt: {str(e)[:80]}")
    try:
        cfg = settings.load()
    except Exception as e:
        logare.info(f"[hooks] setari necitibile, webhook-urile nu pleaca: {str(e)[:80]}")
        return
    urls = [u.strip() for u in (cfg.get("webhook_urls") or []) if u.strip()]
    if not urls:
        return
    permise = cfg.get("webhook_events") or list(EVENIMENTE)
    if eveniment not in permise:
        return
    corp = {"id": uuid.uuid4().hex[:16], "eveniment": eveniment,
            "moment": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sursa": "openvigil"}
    cam = _cam_info(cam_id)
    if cam:
        corp["camera"] = cam
    corp.update(date)
    secret = cfg.get("webhook_secret") or ""
    for u in urls:
        threading.Thread(target=_trimite, args=(u, secret, corp), daemon=True).start()


def test(url=None):
    """Trimite un eveniment de proba, sincron, ca butonul din interfata sa poata raspunde."""
    cfg = settings.load()
    urls = [url] if url else [u.strip() for u in (cfg.get("webhook_urls") or []) if u.strip()]
    if not urls:
        raise ValueError(t("nicio adresa configurată"))
    corp = {"id": uuid.uuid4().hex[:16], "eveniment": "detectie",
            "moment": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "sursa": "openvigil",
            "proba": True, "camera": {"id": 0, "nume": t("Camera de proba"), "grup": "test"},
            "eticheta": "person", "text": t("Eveniment de proba trimis din interfata")}
    return {u: _trimite(u, cfg.get("webhook_secret") or "", corp) for u in urls}
