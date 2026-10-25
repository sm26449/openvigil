"""Teste pentru severitatea evenimentelor (app/rules.severity) — logica pura."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import rules  # noqa: E402


def test_fata_falsa_mereu_critic():
    assert rules.severity("fata_falsa", {}, is_night=False) == "critic"
    assert rules.severity("fata_falsa", {}, is_night=True) == "critic"


def test_persoana_necunoscuta_dupa_ora():
    assert rules.severity("persoana_necunoscuta", {}, is_night=True) == "critic"
    assert rules.severity("persoana_necunoscuta", {}, is_night=False) == "atentie"


def test_persoana_cunoscuta_e_info():
    assert rules.severity("persoana", {"nume": "ion"}, is_night=True) == "info"


def test_numar_cunoscut_vs_necunoscut():
    assert rules.severity("numar", {"numar": "B1", "nume": "duba"}, is_night=True) == "info"
    assert rules.severity("numar", {"numar": "B1"}, is_night=True) == "atentie"
    assert rules.severity("numar", {"numar": "B1"}, is_night=False) == "info"


def test_detectiile_brute_sunt_info():
    # din 2026-09-10, si person: prezenta umana e acoperita de EPISOADE (tranzit/
    # zabovire, zi/noapte) si de "intrus" — triajul nu vede acelasi om de doua ori
    assert rules.severity("detectie", {"eticheta": "person"}, is_night=True) == "info"
    assert rules.severity("detectie", {"eticheta": "person"}, is_night=False) == "info"
    assert rules.severity("detectie", {"eticheta": "car"}, is_night=True) == "info"
    assert rules.severity("detectie", {"eticheta": "chicken"}, is_night=True) == "info"


def test_obiect_urmarit_e_info():
    assert rules.severity("obiect", {"eticheta": "person"}, is_night=True) == "info"
    assert rules.severity("obiect", {"eticheta": "car"}, is_night=True) == "info"


def test_alarma_camera_bruta_e_info():
    # alarmele BRUTE ale camerei raman info (semnalul real e evenimentul 'intrus',
    # confirmat de AI); asa incidentele nu se umplu de fals-pozitive de la pomi/vant
    assert rules.severity("alarma_camera", {"tip": "VMD"}, is_night=True) == "info"
    assert rules.severity("alarma_camera", {}, is_night=True) == "info"
    assert rules.severity("alarma_camera", {"tip": "linedetection"}, is_night=True) == "info"
    assert rules.severity("alarma_camera", {"tip": "fielddetection"}, is_night=True) == "info"


def test_intrus_confirmat_urca():
    # intrus CONFIRMAT de AI la perimetru: om noaptea = critic, ziua = atentie
    assert rules.severity("intrus", {"eticheta": "person"}, is_night=True) == "critic"
    assert rules.severity("intrus", {"eticheta": "person"}, is_night=False) == "atentie"
    # vehicul la perimetru -> atentie (indiferent de ora)
    assert rules.severity("intrus", {"eticheta": "car"}, is_night=True) == "atentie"
    assert rules.severity("intrus", {"eticheta": "truck"}, is_night=False) == "atentie"


def test_zgomot_si_camera_cazuta():
    assert rules.severity("zgomot", {}, is_night=False) == "atentie"
    assert rules.severity("camera_offline", {}, is_night=False) == "atentie"
    assert rules.severity("camera_online", {}, is_night=False) == "info"


def test_necunoscut_default_info():
    assert rules.severity("ceva_nou", {}, is_night=True) == "info"


def test_journal_stocheaza_si_filtreaza_severitatea(monkeypatch):
    monkeypatch.setattr(rules, "_is_night", lambda *a: True)
    rules._journal("detectie", 5, {"eticheta": "person"})            # info (episoadele preiau)
    rules._journal("persoana_necunoscuta", 5, {})                    # critic (noapte)
    rules._journal("zgomot", 5, {"motiv": "prag"})                   # atentie
    assert {e["sev"] for e in rules.journal()} == {"info", "critic", "atentie"}
    assert len(rules.journal(min_sev="atentie")) == 2
    assert len(rules.journal(min_sev="critic")) == 1
    assert rules.journal(min_sev="critic")[0]["tip"] == "persoana_necunoscuta"
