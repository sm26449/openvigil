"""Contractul starii partajate (audit frontend #20): orice proprietate S.<x> scrisa din module
trebuie declarata in app/static/js/state.js, cu valoare initiala si cine o scrie."""
import glob
import os
import re

JS = os.path.join(os.path.dirname(__file__), "..", "app", "static", "js")


def test_proprietatile_scrise_ale_lui_S_sunt_declarate():
    with open(os.path.join(JS, "state.js"), encoding="utf-8") as f:
        declarate = set(re.findall(r"^\s{2}(\w+):", f.read(), re.M))
    scrise = set()
    for p in glob.glob(os.path.join(JS, "*.js")):
        with open(p, encoding="utf-8") as f:
            scrise |= set(re.findall(r"(?<![\w.])S\.(\w+)\s*=(?!=)", f.read()))
    assert scrise, "nu am gasit nicio scriere S.x — s-a schimbat tiparul?"
    assert not scrise - declarate, f"nedeclarate in state.js: {sorted(scrise - declarate)}"
