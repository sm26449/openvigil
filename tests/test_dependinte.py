"""Dependinte cu efect de securitate care trebuie sa ramana cum sunt."""
import importlib.util


def test_olefile_lipseste_deci_flashpix_din_pillow_e_inaccesibil():
    """Pillow < 13 are un aviz critic in decodorul FlashPix (GHSA-5x8w-8jj5-q52h /
    CVE-2026-112398), accesibil DOAR cand e instalat `olefile`. Imaginea nu-l are; daca o
    dependenta noua il aduce, ridica Pillow la >= 13.0.0 inainte de release."""
    assert importlib.util.find_spec("olefile") is None
