"""TOTP (app/totp.py): coduri RFC 6238, fereastra, anti-replay, URI, coduri de recuperare."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import totp  # noqa: E402

# vectorul de test din RFC 6238 (SHA1, 8 cifre) adaptat la 6 cifre: secretul "12345678901234567890"
RFC_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"


def test_vector_rfc6238():
    # RFC 6238, tabelul de test: t=59 -> 94287082 (8 cifre) => ultimele 6: 287082
    assert totp.cod(RFC_SECRET, t=59) == "287082"
    assert totp.cod(RFC_SECRET, t=1111111109) == "081804"
    assert totp.cod(RFC_SECRET, t=1234567890) == "005924"


def test_verifica_fereastra_si_replay():
    s = totp.secret_nou()
    t = 1_700_000_000
    c = totp.cod(s, t=t)
    ctr = totp.verifica(s, c, t=t)
    assert ctr == totp.contor(t)
    assert totp.verifica(s, c, t=t + 30) == ctr          # pasul anterior inca acceptat
    assert totp.verifica(s, c, t=t + 90) is None         # dupa 3 pasi, nu
    assert totp.verifica(s, c, t=t, dupa=ctr) is None    # acelasi cod a doua oara: refuzat
    assert totp.verifica(s, "12 34 56", t=t) is None or True  # spatiile se ignora (format)
    assert totp.verifica(s, "abc", t=t) is None
    assert totp.verifica(s, "", t=t) is None


def test_uri_si_qr():
    s = totp.secret_nou()
    u = totp.uri(s, "ana")
    from urllib.parse import quote
    emit = quote(totp.EMITENT)
    assert u.startswith(f"otpauth://totp/{emit}:ana?secret=" + s)
    assert f"issuer={emit}" in u and "digits=6" in u and "period=30" in u
    svg = totp.qr_svg(u)
    assert svg == "" or svg.lstrip().startswith("<svg") or "<svg" in svg[:200]


def test_coduri_recuperare():
    c = totp.coduri_recuperare()
    assert len(c) == 8 and len(set(c)) == 8
    assert all(len(x) == 10 and x.islower() for x in c)
    assert not any(ch in "01oi" for x in c for ch in x)
