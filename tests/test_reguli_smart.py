"""Reguli pe linii de numarare, zone si intrusi: semantica valorii declansatorului."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

import rules  # noqa: E402


def test_trecere_linie_sens_eticheta():
    ev = {"linie": "poarta", "sens": "intra", "eticheta": "person"}
    m = lambda v: rules._value_match("trecere", v, ev)
    assert m("") and m("poarta") and m("poarta:intra") and m("intra") and m("poarta:intra@person") and m("@person")
    assert not m("alee") and not m("poarta:iese") and not m("iese") and not m("poarta@car") and not m("intra@car")


def test_intrare_zona_si_intrus():
    assert rules._value_match("intrare_zona", "curte", {"zona": "curte", "eticheta": "dog"})
    assert rules._value_match("intrare_zona", "curte@dog", {"zona": "curte", "eticheta": "dog"})
    assert not rules._value_match("intrare_zona", "curte@person", {"zona": "curte", "eticheta": "dog"})
    assert not rules._value_match("intrare_zona", "strada", {"zona": "curte"})
    assert rules._value_match("intrus", "", {"eticheta": "car"})
    assert rules._value_match("intrus", "car", {"eticheta": "car"})
    assert not rules._value_match("intrus", "person", {"eticheta": "car"})


def test_declansatoarele_noi_sunt_acceptate():
    for tip in ("trecere", "intrare_zona", "intrus", "obiect_nou", "obiect_disparut", "parcare_inceput"):
        assert tip in rules.TRIGGER_TYPES
    r = rules.validate({"nume": "t", "declansator": {"tip": "trecere", "valoare": "poarta:intra"},
                        "actiuni": [{"tip": "notificare"}], "moduri": ["plecat"]})
    assert r["declansator"]["valoare"] == "poarta:intra" and r["moduri"] == ["plecat"]
    assert rules.matches(r, "trecere", 5, {"linie": "poarta", "sens": "intra"}, mod_curent="plecat")
    assert not rules.matches(r, "trecere", 5, {"linie": "poarta", "sens": "intra"}, mod_curent="acasa")


def test_sabotajul_camerei_e_de_triat_ziua():
    assert rules.severity("alarma_camera", {"tip": "shelteralarm"}, is_night=False) == "atentie"
    assert rules.severity("alarma_camera", {"tip": "tamperdetection"}, is_night=False) == "atentie"
    assert rules.severity("alarma_camera", {"tip": "shelteralarm"}, is_night=True) == "info"
    assert rules.severity("alarma_camera", {"tip": "VMD"}, is_night=False) == "info"


def test_biciclist_din_viteza_relativa():
    import ai_analytics as a
    assert a.inaltimi_pe_s(300.0, 100.0) == 3.0 and a.inaltimi_pe_s(300.0, None) is None and a.inaltimi_pe_s(300.0, 4) is None
    assert a.este_biciclist("person", 3.0, 2.5)
    assert not a.este_biciclist("person", 0.8, 5.0)          # mers
    assert not a.este_biciclist("person", 3.0, 0.5)          # prea scurt
    assert not a.este_biciclist("car", 3.0, 2.5)             # nu e om
    assert rules._value_match("obiect", "biciclist", {"eticheta": "person", "biciclist": True})
    assert not rules._value_match("obiect", "biciclist", {"eticheta": "person"})
    assert rules._value_match("obiect", "person", {"eticheta": "person", "biciclist": True})
