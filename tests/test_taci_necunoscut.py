"""ai_track._taci_necunoscut: pe vreme rea + noapte, candidatii „necunoscut" ai scenei nu se mai
jurnalizeaza/notifica (doar numarati) — restul etichetelor si restul regimurilor trec normal."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import ai_track  # noqa: E402

T = ai_track._taci_necunoscut


def test_taci_doar_necunoscut_vreme_rea_noapte():
    assert T("necunoscut", True, True) is True          # exact cazul tintit


def test_nu_taci_etichete_reale():
    assert T("person", True, True) is False             # un real trece mereu
    assert T("car", True, True) is False
    assert T("frunze mișcate de vânt", True, True) is False  # alt neutru, nu „necunoscut"


def test_nu_taci_fara_vreme_rea_sau_ziua():
    assert T("necunoscut", False, True) is False        # vreme buna -> jurnalizam
    assert T("necunoscut", True, False) is False         # ziua -> jurnalizam
    assert T("necunoscut", False, False) is False
