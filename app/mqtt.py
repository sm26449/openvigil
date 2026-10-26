#!/usr/bin/env python3
"""Puntea MQTT — evenimentele sistemului, publicate pentru Home Assistant / Node-RED /
orice altceva vorbeste MQTT, plus actiunea "mqtt" din motorul de reguli.

Topicuri (baza configurabila, implicit "nvr"):
  nvr/stare                     online/offline (retained, cu Last Will — brokerul
                                anunta singur "offline" daca serverul moare)
  nvr/evenimente/<tip>          fiecare eveniment, JSON (detectie, persoana, numar...)
  nvr/camera/<id>/<tip>         acelasi eveniment, pe topicul camerei
  <topic liber>                 actiunea "mqtt" a unei reguli publica orice text pe
                                orice topic — de aici se aprind luminile pe zone
  nvr/mod                       modul curent acasa/plecat/noapte (retained)
  nvr/mod/set       (intrare)   acasa | plecat | noapte — comuta modul (de la Home
                                Assistant, PV-stack, prezenta telefonului in Wi-Fi)

Conexiunea e lenesa si rezistenta: paho reconecteaza singur, iar cand brokerul nu e
disponibil mesajele se pierd linistit (evenimentele raman oricum in jurnalul local —
MQTT e o punte, nu sursa de adevar).
"""
import json
import threading
import time

import settings
import logare
import config as C
from i18n import t

_client = None
_init_lock = threading.Lock()
state = {"connected": False, "error": None, "published": 0, "broker": ""}


def enabled():
    return bool(settings.get("mqtt_enabled") and settings.get("mqtt_host"))


def _base():
    return (settings.get("mqtt_topic") or "nvr").strip("/")


def _on_connect(client, userdata, flags, rc, props=None):
    state["connected"] = (getattr(rc, "value", rc) == 0)
    state["error"] = None if state["connected"] else str(rc)
    if state["connected"]:
        client.publish(f"{_base()}/stare", "online", qos=1, retain=True)
        client.subscribe(f"{_base()}/mod/set", qos=1)
        logare.info(f"[mqtt] conectat la {state['broker']}")
        try:
            import mod
            mod.publica()
        except Exception as e:
            logare.info(f"[mqtt] mod: {str(e)[:60]}")


def _on_message(client, userdata, msg):
    """Singura intrare acceptata: <baza>/mod/set cu acasa/plecat/noapte."""
    try:
        if msg.topic != f"{_base()}/mod/set":
            return
        import mod
        mod.set(msg.payload.decode().strip().lower(), cine="mqtt", sursa="mqtt")
    except Exception as e:
        logare.info(f"[mqtt] mod/set: {str(e)[:80]}")


def _on_disconnect(client, userdata, flags, rc, props=None):
    state["connected"] = False


def _get_client():
    global _client
    with _init_lock:
        if _client is None:
            import paho.mqtt.client as paho
            c = paho.Client(paho.CallbackAPIVersion.VERSION2,
                            client_id="openvigil", clean_session=True)
            user = settings.get("mqtt_user") or ""
            if user:
                c.username_pw_set(user, settings.get("mqtt_pass") or "")
            c.will_set(f"{_base()}/stare", "offline", qos=1, retain=True)
            c.on_connect = _on_connect
            c.on_disconnect = _on_disconnect
            c.on_message = _on_message
            host = settings.get("mqtt_host")
            port = int(settings.get("mqtt_port") or 1883)
            state["broker"] = f"{host}:{port}"
            c.connect_async(host, port, keepalive=30)
            c.loop_start()      # thread propriu; reconectare automata
            _client = c
    return _client


def reconfigure():
    """Dupa schimbarea setarilor: inchide conexiunea veche; urmatoarea publicare
    deschide una noua cu setarile curente."""
    global _client
    with _init_lock:
        if _client is not None:
            try:
                _client.publish(f"{_base()}/stare", "offline", qos=1, retain=True)
                _client.loop_stop()
                _client.disconnect()
            except Exception:
                pass
            _client = None
        state.update(connected=False, error=None)
    if enabled():
        _get_client()


def warmup():
    if enabled():
        _get_client()


def publish(topic, payload, retain=False):
    """Publica pe un topic ABSOLUT (actiunea "mqtt" a regulilor) sau relativ la baza
    daca nu contine '/'. Nu blocheaza si nu arunca — un broker cazut nu opreste nimic."""
    if not enabled():
        return False
    try:
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, ensure_ascii=False)
        r = _get_client().publish(str(topic), payload, qos=0, retain=retain)
        state["published"] += 1
        return r.rc == 0
    except Exception as e:
        state["error"] = str(e)[:80]
        return False


def publish_event(eveniment, cam_id, date):
    """Chemata din hooks.emit pentru orice eveniment."""
    if not enabled():
        return
    payload = {"eveniment": eveniment,
               "moment": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if cam_id is not None:
        payload["camera"] = cam_id
    for k, v in (date or {}).items():
        if isinstance(v, (str, int, float, bool, list)) and len(str(v)) <= 200:
            payload["subtip" if k == "tip" else k] = v
    publish(f"{_base()}/evenimente/{eveniment}", payload)
    if cam_id is not None:
        publish(f"{_base()}/camera/{cam_id}/{eveniment}", payload)


def test():
    """Conectare + publicare sincrona de proba — pentru butonul din Setari."""
    if not settings.get("mqtt_host"):
        raise ValueError(t("completează adresa brokerului"))
    reconfigure()
    if not enabled():
        raise ValueError(t("pornește întâi MQTT (bifa Activat)"))
    c = _get_client()
    t0 = time.time()
    while not state["connected"] and time.time() - t0 < 6:
        time.sleep(0.1)
    if not state["connected"]:
        raise RuntimeError(state.get("error") or t("brokerul nu raspunde (adresa/portul/user/parola?)"))
    c.publish(f"{_base()}/proba",
              json.dumps({"mesaj": f"salut de la {C.NUME}",
                          "moment": time.strftime("%H:%M:%S")}, ensure_ascii=False))
    return dict(state)
