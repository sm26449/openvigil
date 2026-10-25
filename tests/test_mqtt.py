"""Teste pentru app/mqtt.py — construirea payload-urilor si logica de activare.

Brokerul nu se atinge: publish() se inlocuieste cu un colector.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import mqtt  # noqa: E402
import settings  # noqa: E402


@pytest.fixture(autouse=True)
def _mqtt_settings(monkeypatch):
    vals = {"mqtt_enabled": True, "mqtt_host": "broker.local",
            "mqtt_port": 1883, "mqtt_user": "", "mqtt_pass": "", "mqtt_topic": "nvr"}
    monkeypatch.setattr(settings, "get", lambda k: vals.get(k))
    yield vals


def test_enabled_cere_host(_mqtt_settings, monkeypatch):
    assert mqtt.enabled()
    _mqtt_settings["mqtt_host"] = ""
    assert not mqtt.enabled()
    _mqtt_settings["mqtt_host"] = "x"
    _mqtt_settings["mqtt_enabled"] = False
    assert not mqtt.enabled()


def test_publish_event_construieste_topicuri(monkeypatch):
    pub = []
    monkeypatch.setattr(mqtt, "publish", lambda t, p, retain=False: pub.append((t, p)))
    mqtt.publish_event("persoana", 101, {"nume": "ion", "similaritate": 0.9})
    assert [t for t, _ in pub] == ["nvr/evenimente/persoana", "nvr/camera/101/persoana"]
    assert pub[0][1]["nume"] == "ion" and pub[0][1]["camera"] == 101


def test_publish_event_redenumeste_subtipul(monkeypatch):
    pub = []
    monkeypatch.setattr(mqtt, "publish", lambda t, p, retain=False: pub.append((t, p)))
    mqtt.publish_event("alarma_camera", 3, {"tip": "VMD"})
    assert pub[0][1]["subtip"] == "VMD" and pub[0][1]["eveniment"] == "alarma_camera"


def test_publish_event_fara_camera(monkeypatch):
    pub = []
    monkeypatch.setattr(mqtt, "publish", lambda t, p, retain=False: pub.append((t, p)))
    mqtt.publish_event("camera_offline", None, {})
    assert len(pub) == 1 and "camera" not in pub[0][1]


def test_publish_dezactivat_nu_face_nimic(_mqtt_settings):
    _mqtt_settings["mqtt_enabled"] = False
    assert mqtt.publish("orice/topic", "x") is False


def test_publish_event_taie_valorile_lungi(monkeypatch):
    pub = []
    monkeypatch.setattr(mqtt, "publish", lambda t, p, retain=False: pub.append((t, p)))
    mqtt.publish_event("detectie", 1, {"eticheta": "dog", "gunoi": "x" * 500})
    assert "gunoi" not in pub[0][1] and pub[0][1]["eticheta"] == "dog"
