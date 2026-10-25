"""Sursele de flux fara parola in `ps` (prin go2rtc, cu rezerva directa) si comutatorul
„NVR retras”: nimic nu mai incearca NVR-ul cand e scos din functiune."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import audio_level  # noqa: E402
import notify  # noqa: E402
import record  # noqa: E402
import settings  # noqa: E402

CAM = {"id": 6, "ip": "192.168.1.104", "user": "admin", "pass": "p@ss", "nvr_channel": 6}


def test_inregistrarea_prefera_go2rtc_apoi_camera_apoi_nvr(monkeypatch):
    monkeypatch.setattr(settings, "nvr_retras", lambda: False)
    urls = record._sources(CAM)
    assert [e for _, e in urls] == ["prin go2rtc", "direct de pe camera", "prin NVR"]
    assert urls[0][0] == "rtsp://127.0.0.1:8554/cam6_main"          # fara user:parola
    assert "p%40ss@192.168.1.104" in urls[1][0] and "/101" in urls[1][0]
    assert urls[2][0].endswith("/Streaming/Channels/601")


def test_nvr_retras_scoate_rezerva_prin_nvr(monkeypatch):
    monkeypatch.setattr(settings, "nvr_retras", lambda: True)
    assert [e for _, e in record._sources(CAM)] == ["prin go2rtc", "direct de pe camera"]


def test_camera_fara_canal_nvr_nu_are_rezerva_nvr(monkeypatch):
    monkeypatch.setattr(settings, "nvr_retras", lambda: False)
    assert [e for _, e in record._sources({**CAM, "nvr_channel": None})] == ["prin go2rtc", "direct de pe camera"]


def test_nivelul_audio_citeste_prin_go2rtc_cu_rezerva_directa():
    urls = audio_level.AudioLevel(CAM)._urls() if hasattr(audio_level, "AudioLevel") else None
    if urls is None:   # numele clasei difera: luam prima clasa cu _urls
        cls = next(v for v in vars(audio_level).values() if isinstance(v, type) and hasattr(v, "_urls"))
        urls = cls(CAM)._urls()
    assert urls[0] == "rtsp://127.0.0.1:8554/cam6"
    assert urls[1].startswith("rtsp://admin:p%40ss@192.168.1.104:554/Streaming/Channels/102")


def test_watchdog_nu_mai_supravegheaza_nvr_ul_retras(monkeypatch):
    monkeypatch.setattr(settings, "nvr_retras", lambda: True)
    apeluri = []
    monkeypatch.setattr(notify.requests, "Session", lambda: apeluri.append(1))
    monkeypatch.setattr(notify, "send", lambda *a, **k: apeluri.append("send"))
    notify._states["nvr"] = False
    notify._check_nvr()
    assert apeluri == [] and notify.stare_nvr() is None


def test_istoricul_nvr_dispare_cand_e_retras(monkeypatch):
    import server
    monkeypatch.setattr(settings, "nvr_retras", lambda: True)
    monkeypatch.setattr(server, "load_ext", lambda: [CAM])
    assert server.nvr_history_cam(6) is None
    monkeypatch.setattr(settings, "nvr_retras", lambda: False)
    assert server.nvr_history_cam(6)["main"] == 601


def test_revenirea_la_go2rtc_doar_dupa_prag_si_la_inceput_de_segment():
    t0 = 1_000_000 - (1_000_000 % 300)          # exact un inceput de segment
    assert not record.revenire_la_preferata(0, t0 - 9999, t0 + 3)          # deja pe preferata
    assert not record.revenire_la_preferata(1, t0 - 100, t0 + 3)           # prea devreme
    assert record.revenire_la_preferata(1, t0 - 700, t0 + 3)               # tarziu + segment nou
    assert not record.revenire_la_preferata(1, t0 - 700, t0 + 120)         # in mijlocul segmentului


def test_camera_demo_inregistreaza_doar_prin_go2rtc(monkeypatch):
    monkeypatch.setattr(settings, "nvr_retras", lambda: True)
    urls = record._sources({"id": 9, "vendor": "demo", "demo": "testsrc2", "ip": "demo-testsrc2", "user": "", "pass": ""})
    assert urls == [("rtsp://127.0.0.1:8554/cam9_main", "prin go2rtc")]
