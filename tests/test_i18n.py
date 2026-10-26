"""Interfata multilingva (app/i18n.py): t() cu parametri si plural, traducerea paginii HTML
pe server, alegerea limbii si completitudinea dictionarelor fata de textele din cod."""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import i18n  # noqa: E402
import i18n_check  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture
def en(monkeypatch):
    monkeypatch.setitem(i18n._dict, "en", {
        "Adaugă camera": "Add camera",
        "Camera {nume} nu răspunde": "Camera {nume} is not responding",
        "{n} camere": {"one": "{n} camera", "other": "{n} cameras"},
        "Închide": "Close",
    })
    monkeypatch.setitem(i18n._nume, "en", "English")
    i18n._pagini.clear()
    yield
    i18n._pagini.clear()


def test_t_traduce_si_cade_pe_cheie(en):
    assert i18n.t("Adaugă camera", "en") == "Add camera"
    assert i18n.t("Adaugă camera", "ro") == "Adaugă camera"
    assert i18n.t("text fără traducere", "en") == "text fără traducere"


def test_t_parametri_dupa_traducere(en):
    assert i18n.t("Camera {nume} nu răspunde", "en", nume="Curte") == "Camera Curte is not responding"
    assert i18n.t("Camera {nume} nu răspunde", "ro", nume="Curte") == "Camera Curte nu răspunde"
    # un parametru necunoscut ramane la vedere, nu arunca
    assert i18n.t("Camera {nume} nu răspunde", "ro") == "Camera {nume} nu răspunde"


def test_plural(en):
    assert i18n.t("{n} camere", "en", n=1) == "1 camera"
    assert i18n.t("{n} camere", "en", n=4) == "4 cameras"
    assert i18n.t("{n} camere", "ro", n=4) == "4 camere"
    assert [i18n.plural("ro", n) for n in (1, 2, 19, 20, 101, 0)] == ["one", "few", "few", "other", "other", "few"]
    assert [i18n.plural("en", n) for n in (1, 2, 0)] == ["one", "other", "other"]


def test_pagina_traduce_text_si_atribute(en):
    src = ('<!DOCTYPE html><html lang="ro"><head><title>OpenVigil</title></head><body>'
           '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M1 1"/></svg>'
           '<button title="Închide">\n  Adaugă camera\n</button>'
           '<input placeholder="Închide"><p>Camera &lt;id&gt; e &amp; rămâne</p>'
           '<code>&lt;id&gt;</code><pre>  Închide  </pre></body></html>')
    out = i18n.pagina(src, "en")
    assert '<html lang="en">' in out
    assert 'viewBox="0 0 24 24"' in out                          # tag-urile ies octet cu octet
    assert '<button title="Close">\n  Add camera\n</button>' in out   # spatiile din jur raman
    assert 'placeholder="Close"' in out
    assert "<p>Camera &lt;id&gt; e &amp; rămâne</p>" in out      # entitatile raman entitati
    assert "<code>&lt;id&gt;</code>" in out and "<pre>  Închide  </pre>" in out   # pre/code netraduse
    # in romana documentul iese identic cu sursa
    assert i18n.pagina(src, "ro") == src


def test_extrage_html_chei_normalizate():
    chei = i18n.extrage_html('<p title="Cu &quot;ghilimele&quot;">  Două\n   rânduri </p><b>✕</b><i>42</i>')
    assert chei == ['Cu "ghilimele"', "Două rânduri"]           # fara simboluri si numere


def test_index_html_in_romana_iese_identic():
    with open(os.path.join(ROOT, "app", "static", "index.html"), encoding="utf-8") as f:
        src = f.read()
    i18n._pagini.clear()
    assert i18n.pagina(src, "ro") == src


def test_limba_curenta_ordine(monkeypatch, en):
    flask = pytest.importorskip("flask")
    app = flask.Flask("t")
    monkeypatch.setattr(i18n.C, "LIMBA", "ro", raising=False)
    pref = {"v": None}
    monkeypatch.setattr(i18n, "pref_utilizator", lambda: pref["v"])
    with app.test_request_context("/", headers={"Accept-Language": "en-US,en;q=0.9"}):
        assert i18n.limba_curenta() == "en"                      # browserul
    with app.test_request_context("/", headers={"Accept-Language": "de-DE,de;q=0.9"}):
        assert i18n.limba_curenta() == "ro"                      # limba lipsa -> NVR_LANG
    with app.test_request_context("/", headers={"Accept-Language": "en"}):
        flask.request.cookies = {"lang": "ro"}
        assert i18n.limba_curenta() == "ro"                      # cookie-ul bate browserul
    pref["v"] = "en"
    with app.test_request_context("/", headers={"Cookie": "lang=ro"}):
        assert i18n.limba_curenta() == "en"                      # contul bate cookie-ul
    with app.test_request_context("/?lang=ro"):
        assert i18n.limba_curenta() == "ro"                      # URL-ul bate tot
        assert flask.g.limba_cookie == "ro"
    with app.test_request_context("/?lang=xx"):
        assert i18n.limba_curenta() == "en"                      # cod necunoscut: ignorat


def test_dictionarele_acopera_textele_din_cod():
    """Fiecare text din index.html, routes_auth.py, t() din JS si Python are intrare in
    fiecare dictionar (altfel ar aparea in romana in interfata engleza), iar parametrii
    {x} se potrivesc. Cheile nefolosite sunt doar avertisment (tabele cu etichete)."""
    chei, greseli = i18n_check.toate_cheile()
    assert not greseli, greseli
    assert len(chei) > 100
    for fn in sorted(os.listdir(i18n.DIR_APP)):
        if not fn.endswith(".json") or fn == "ro.json":
            continue
        with open(os.path.join(i18n.DIR_APP, fn), encoding="utf-8") as f:
            dic = json.load(f)
        erori, _ = i18n_check.verifica(dic, chei, fn)
        assert not erori, "\n".join(erori[:40]) + (f"\n... {len(erori)} în total" if len(erori) > 40 else "")
