"""Coada de joburi (app/jobs.py): rulare, progres, erori, dedup, anulare, repornire, curatare."""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import jobs  # noqa: E402


@pytest.fixture(autouse=True)
def _lucratori(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "DIR", str(tmp_path / "joburi"))
    jobs._handlers.clear()
    jobs.start(workers=2)
    yield
    jobs.stop()


def test_job_ruleaza_si_intoarce_rezultat():
    jobs.register("aduna", lambda j: {"suma": j.parametri["a"] + j.parametri["b"]})
    j = jobs.submit("aduna", {"a": 2, "b": 3}, who="admin")
    assert j["stare"] == "asteapta" and j["creat_de"] == "admin" and j["parametri"] == {"a": 2, "b": 3}
    j = jobs.asteapta(j["id"])
    assert j["stare"] == "gata" and j["progres"] == 100 and j["rezultat"] == {"suma": 5}
    assert j["pornit_t"] and j["terminat_t"]


def test_tip_necunoscut():
    with pytest.raises(ValueError):
        jobs.submit("nu-exista")


def test_progres_mesaj_si_fisier(tmp_path):
    def h(j):
        j.progres(10, "contactez camera")
        p = j.cale("txt")
        with open(p, "w") as f:
            f.write("salut")
        j.fisier(p, "rezultat.txt", "text/plain")
        j.progres(90)
        return {}
    jobs.register("f", h)
    j = jobs.asteapta(jobs.submit("f")["id"])
    assert j["stare"] == "gata" and j["are_fisier"] and j["nume_fisier"] == "rezultat.txt"
    assert "fisier" not in j                              # calea nu iese din server
    cale, nume, mime = jobs.cale_fisier(j["id"])
    assert open(cale).read() == "salut" and mime == "text/plain"
    assert j["mesaj"] == "contactez camera"


def test_joberror_e_eroare_pentru_utilizator_si_bugul_nu_omoara_lucratorul():
    def rau(j):
        raise jobs.JobError("nu exista inregistrari in interval")

    def bug(j):
        raise KeyError("x")
    jobs.register("rau", rau)
    jobs.register("bug", bug)
    jobs.register("ok", lambda j: {"ok": 1})
    a = jobs.asteapta(jobs.submit("rau")["id"])
    b = jobs.asteapta(jobs.submit("bug")["id"])
    c = jobs.asteapta(jobs.submit("ok")["id"])
    assert a["stare"] == "eroare" and a["mesaj"] == "nu exista inregistrari in interval"
    assert b["stare"] == "eroare" and b["mesaj"].startswith("eroare interna")
    assert c["stare"] == "gata"


def test_dedup_job_identic_in_coada():
    poarta = threading.Event()
    jobs.register("lent", lambda j: poarta.wait(5) and {})
    a = jobs.submit("lent", {"x": 1})
    b = jobs.submit("lent", {"x": 1})           # identic, inca ruleaza -> acelasi
    c = jobs.submit("lent", {"x": 2})           # alti parametri -> job nou
    assert a["id"] == b["id"] and c["id"] != a["id"]
    poarta.set()
    assert jobs.asteapta(a["id"])["stare"] == "gata"
    d = jobs.submit("lent", {"x": 1})           # primul s-a terminat -> job nou
    assert d["id"] != a["id"]


def test_anulare_in_asteptare_si_in_rulare():
    poarta = threading.Event()

    def lent(j):
        poarta.set()
        for _ in range(100):
            if j.anulat:
                raise jobs.JobError("oprit")
            time.sleep(0.02)
        return {}
    jobs.register("lent", lent)
    jobs.register("blocat", lambda j: time.sleep(2) or {})
    a = jobs.submit("lent", {"n": 1})
    poarta.wait(2)
    assert jobs.cancel(a["id"]) is True
    assert jobs.asteapta(a["id"])["stare"] == "anulat"
    # ocupam ambii lucratori, al treilea job asteapta -> anulat inainte sa porneasca
    jobs.submit("blocat", {"n": 1})
    jobs.submit("blocat", {"n": 2})
    c = jobs.submit("lent", {"n": 3})
    assert jobs.get(c["id"])["stare"] == "asteapta"
    assert jobs.cancel(c["id"]) is True
    assert jobs.get(c["id"])["stare"] == "anulat"
    assert jobs.cancel(c["id"]) is False          # deja final
    assert jobs.cancel("nuexista") is False


def test_lista_pe_utilizator_si_active():
    jobs.register("ok", lambda j: {})
    jobs.asteapta(jobs.submit("ok", {"a": 1}, who="ana")["id"])
    jobs.asteapta(jobs.submit("ok", {"a": 2}, who="ion")["id"])
    assert len(jobs.lista()) == 2
    assert [j["creat_de"] for j in jobs.lista(who="ana")] == ["ana"]
    assert jobs.lista(doar_active=True) == []


def test_repornirea_marcheaza_joburile_ramase():
    jobs.register("ok", lambda j: {})
    jobs.stop()
    j = jobs.submit("ok", {"r": 1})            # fara lucratori: ramane in asteptare
    assert jobs.get(j["id"])["stare"] == "asteapta"
    jobs.start(workers=1)
    j2 = jobs.get(j["id"])
    assert j2["stare"] == "eroare" and "repornit" in j2["mesaj"]


def test_curatare_fisiere_si_randuri(tmp_path):
    def h(j):
        p = j.cale("bin")
        open(p, "wb").write(b"x")
        j.fisier(p, "x.bin")
        return {}
    jobs.register("f", h)
    j = jobs.asteapta(jobs.submit("f")["id"])
    assert j["are_fisier"]
    assert jobs.curata() == (0, 0)                       # proaspat: nimic
    fis, rand = jobs.curata(acum=time.time() + jobs.FILE_TTL + 1)
    assert fis == 1 and rand == 0 and not jobs.get(j["id"])["are_fisier"]
    fis, rand = jobs.curata(acum=time.time() + jobs.ROW_TTL + 1)
    assert rand == 1 and jobs.get(j["id"]) is None


def test_eroarea_ffmpeg_nu_scapa_parola_camerei():
    """ffmpeg repeta URL-ul de intrare in erori; mesajul jobului ajunge pe ecran si in jurnal."""
    import server
    err = (b"[tcp @ 0x1] Connection to tcp://192.168.1.253:554?timeout=0 failed: No route to host\n"
           b"Error opening input file rtsp://admin:Parola%21secreta@192.168.1.253:554/Streaming/tracks/601.\n"
           b"Error opening input files: No route to host\n")
    curat = server._fara_parole(err)
    assert "Parola" not in curat and "admin:" not in curat
    assert "rtsp://***@192.168.1.253:554/Streaming/tracks/601" in curat
    assert "tcp://192.168.1.253:554?timeout=0 failed" in curat   # fara user:parola ramane cum e
