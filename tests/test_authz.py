"""Autorizare per-camera pe dosarele de caz — un operator restrans nu trebuie sa vada
cazuri cu probe de pe camere neautorizate. (Reviewerii au semnalat clasa asta de bug;
tinta reala e /api/cases, care nu filtra.)"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import routes_incidents  # noqa: E402  (_case_visible s-a mutat aici la spargerea server.py)

_case_visible = routes_incidents._case_visible


def _caz(*cams):
    return {"id": "x", "probe": [{"cam": c} for c in cams]}


def test_admin_vede_tot():
    # vis = None => admin/viewer nerestrans -> vede orice caz
    assert _case_visible(_caz(5, 9), None) is True


def test_operator_vede_doar_camerele_lui():
    vis = {5, 6}
    assert _case_visible(_caz(5), vis) is True
    assert _case_visible(_caz(5, 6), vis) is True
    assert _case_visible(_caz(5, 9), vis) is False   # 9 nu e permisa -> ascuns tot cazul
    assert _case_visible(_caz(9), vis) is False


def test_caz_gol_e_vizibil():
    # caz proaspat, fara probe -> nimic de scurs, vizibil
    assert _case_visible({"probe": []}, {5}) is True
    assert _case_visible({}, {5}) is True
