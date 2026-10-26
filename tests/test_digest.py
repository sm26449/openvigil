"""Teste pentru app/digest.py — rezumatul zilnic."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import digest  # noqa: E402
import rules  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(rules, "_is_night", lambda *a: True)   # noapte -> necunoscut = critic


def _seed():
    rules._journal("detectie", 5, {"eticheta": "chicken"})        # info
    rules._journal("detectie", 5, {"eticheta": "person"})         # atentie (noapte)
    rules._journal("persoana_necunoscuta", 7, {})                 # critic (noapte)
    rules._journal("persoana_necunoscuta", 7, {})                 # critic (acelasi ep)
    rules._journal("numar", 3, {"numar": "B123XYZ"})              # atentie (noapte)


def test_build_azi_numara_pe_severitate():
    _seed()
    zi = rules.journal()[0]["t"][:10]
    d = digest.build(day=zi)
    assert d["total"] == 5
    assert d["severitate"]["critic"] == 2
    assert d["severitate"]["atentie"] >= 1
    assert d["incidente"] == d["severitate"]["critic"] + d["severitate"]["atentie"]


def test_build_lista_critice_deduplicata():
    _seed()
    zi = rules.journal()[0]["t"][:10]
    d = digest.build(day=zi)
    # doua persoana_necunoscuta consecutive pe aceeasi camera -> un singur episod critic
    assert len(d["critice"]) == 1
    assert d["critice"][0]["cam"] == 7
    assert d["critice"][0]["tip"] == "persoana_necunoscuta"


def test_securitate_numara_categoriile():
    _seed()
    zi = rules.journal()[0]["t"][:10]
    d = digest.build(day=zi)
    assert d["securitate"]["persoana_necunoscuta"] == 2
    assert d["securitate"]["numar"] == 1
    assert d["securitate"]["fata_falsa"] == 0


def test_top_camere_si_activitate():
    _seed()
    zi = rules.journal()[0]["t"][:10]
    d = digest.build(day=zi)
    cams = dict(d["top_camere"])
    assert cams.get(5) == 2 and cams.get(7) == 2 and cams.get(3) == 1
    assert d["prima_activitate"] and d["ultima_activitate"]
    assert d["ora_varf"] is not None


def test_zi_goala():
    d = digest.build(day="2020-01-01")
    assert d["total"] == 0 and d["incidente"] == 0
    assert d["critice"] == [] and d["top_camere"] == []
    assert d["prima_activitate"] is None and d["ora_varf"] is None


def test_filtru_pe_camere_vizibile():
    _seed()
    zi = rules.journal()[0]["t"][:10]
    # operator care vede doar camera 5 -> nu vede criticele de pe cam7
    d = digest.build(day=zi, visible={5})
    assert d["severitate"]["critic"] == 0
    assert all(c["cam"] == 5 for c in d["critice"])
    assert dict(d["top_camere"]).keys() <= {5}


# ---- mesajul zilnic pentru Telegram (digest.text) ----
def test_text_cu_toate_sursele(monkeypatch):
    monkeypatch.setattr(digest, "build", lambda day=None, visible=None: {
        "total": 42, "severitate": {"critic": 1, "atentie": 3, "info": 38}, "incidente": 4,
        "prima_activitate": "06:10", "ultima_activitate": "22:40", "ora_varf": ("18:00", 9),
        "top_camere": [(13, 20), (14, 9)], "securitate": {},
        "critice": [{"ora": "23:10", "cam": 13, "tip": "intrus", "detaliu": "person", "de_cate_ori": 2}]})
    monkeypatch.setattr(digest, "_nume_camere", lambda: {13: "Strada", 14: "Poarta"})
    import faces, plates, continuity, snaps
    monkeypatch.setattr(faces, "history", lambda limit=100, nume=None: [{"t": "2026-10-25 10:00:00", "nume": "ana"}, {"t": "2026-10-25 11:00:00", "nume": "ana"}, {"t": "2026-10-24 11:00:00", "nume": "ion"}])
    monkeypatch.setattr(plates, "known", lambda: {"B321XYZ": "noi"})
    monkeypatch.setattr(plates, "reads", lambda limit=100: [{"t": "2026-10-25 09:00:00", "numar": "XX11YYY"}, {"t": "2026-10-25 09:30:00", "numar": "B321XYZ", "nume": "noi"}])
    monkeypatch.setattr(continuity, "gaps", lambda cam, day: [{"sec": 600}] if cam == 13 else [])
    monkeypatch.setattr(continuity, "live", lambda: [{"id": 14, "mod": "continuous", "scrie": False}, {"id": 13, "mod": "continuous", "scrie": True}])
    import server
    monkeypatch.setattr(server, "load_ext", lambda: [{"id": 13, "record": "continuous"}, {"id": 14, "record": "continuous"}])
    monkeypatch.setattr(snaps, "list_day", lambda day, limit=None: [{"cam": 3, "label": "shelteralarm"}] * 12 + [{"cam": 13, "label": "VMD"}])
    t = digest.text("2026-10-25")
    assert "Rezumatul zilei 2026-10-25" in t and "42 de evenimente" in t and "1 critice" in t
    assert "06:10–22:40" in t and "vârf la 18:00" in t and "Strada (20)" in t
    assert "văzuți: ana×2" in t and "ion" not in t                    # doar ziua ceruta
    assert "mașini necunoscute: XX11YYY×1" in t and "B321XYZ" not in t       # cunoscuta nu e „necunoscuta”
    assert "goluri de înregistrare: Strada (1, 10 min)" in t
    assert "nu înregistrează acum: Poarta" in t
    assert "sabotaj raportat: cam 3×12" in t
    assert "critice:" in t and "23:10 Strada: intrus · person ×2" in t


def test_text_supravietuieste_surselor_cazute(monkeypatch):
    monkeypatch.setattr(digest, "build", lambda day=None, visible=None: {"total": 0, "severitate": {"critic": 0, "atentie": 0, "info": 0}, "incidente": 0, "critice": []})
    monkeypatch.setattr(digest, "_nume_camere", lambda: {})
    import faces, plates, continuity
    monkeypatch.setattr(faces, "history", lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(plates, "reads", lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(continuity, "live", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    t = digest.text("2026-10-25")
    assert t.startswith("📋 Rezumatul zilei 2026-10-25") and "0 evenimente" in t
