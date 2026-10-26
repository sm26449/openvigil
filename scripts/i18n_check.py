#!/usr/bin/env python3
"""Verifica dictionarele de traducere fata de textele din cod.

Cheile sunt textele romanesti (vezi app/i18n.py). Le adunam din:
  - index.html si formularele din routes_auth.py (noduri de text + atribute vizibile),
  - JavaScript: t('...') / t("...") / t(`...`) fara ${},
  - Python: t("...") / t('...') (si t(f"...") e semnalat ca greseala: parametrii merg
    prin t("... {x}", x=...), nu prin f-string, altfel cheia nu mai e gasita).
Apoi, pentru fiecare limba: ce lipseste (eroare), ce e in plus (avertisment: poate fi o
cheie folosita dinamic, din tabele) si placeholder-ele {x} care nu se potrivesc (eroare).

    python3 scripts/i18n_check.py                 # toate limbile din app/static/i18n
    python3 scripts/i18n_check.py --dict x.json   # un dictionar oarecare (ex. unul in lucru)
    python3 scripts/i18n_check.py --chei          # doar lista cheilor, una pe rand
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "app"))
sys.path.insert(0, ROOT)
import i18n  # noqa: E402

JS_DIR = os.path.join(ROOT, "app", "static", "js")
PY_DIR = os.path.join(ROOT, "app")
INDEX = os.path.join(ROOT, "app", "static", "index.html")
AUTH = os.path.join(ROOT, "app", "routes_auth.py")

# t('...'), t("..."), t(`...`) — fara interpolare in cheie; \\x pentru ghilimele scapate
_JS = re.compile(r"""(?<![\w.$])t\(\s*(?:'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)"|`((?:\\.|[^`\\$])*)`)""")
_PY = re.compile(r"""(?<![\w.])t\(\s*(?:'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)")""")
_PY_F = re.compile(r"""(?<![\w.])t\(\s*f['"]""")
_PARAM = re.compile(r"\{(\w+)\}")
_TRIPLE = re.compile(r'"""(.*?)"""', re.S)


def _unescape(s):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), s)


def chei_html():
    chei = {}
    with open(INDEX, encoding="utf-8") as f:
        for k in i18n.extrage_html(f.read()):
            chei.setdefault(k, "app/static/index.html")
    with open(AUTH, encoding="utf-8") as f:
        for m in _TRIPLE.finditer(f.read()):
            if "</" not in m.group(1):
                continue                         # docstring, nu sablon HTML
            for k in i18n.extrage_html(m.group(1)):
                if "{" in k and "}" in k and _PARAM.fullmatch(k):
                    continue                     # {err}, {rez}, {limbi}: campuri completate de server
                chei.setdefault(k, "app/routes_auth.py")
    return chei


def chei_js():
    chei = {}
    for fn in sorted(os.listdir(JS_DIR)):
        if not fn.endswith(".js"):
            continue
        with open(os.path.join(JS_DIR, fn), encoding="utf-8") as f:
            for m in _JS.finditer(f.read()):
                k = _unescape(next(g for g in m.groups() if g is not None))
                chei.setdefault(k, f"app/static/js/{fn}")
    return chei


def chei_py():
    chei, greseli = {}, []
    for fn in sorted(os.listdir(PY_DIR)):
        if not fn.endswith(".py") or fn == "i18n.py":
            continue
        with open(os.path.join(PY_DIR, fn), encoding="utf-8") as f:
            # fara liniile-comentariu: exemplele din ele nu sunt chei
            s = "\n".join("" if ln.lstrip().startswith("#") else ln for ln in f.read().split("\n"))
        for m in _PY.finditer(s):
            k = _unescape(next(g for g in m.groups() if g is not None))
            chei.setdefault(k, f"app/{fn}")
        for m in _PY_F.finditer(s):
            greseli.append(f"app/{fn}:{s[:m.start()].count(chr(10)) + 1}: t(f\"...\") — cheia nu mai e un text fix")
    return chei, greseli


def toate_cheile():
    chei, greseli = chei_py()
    for k, unde in list(chei_html().items()) + list(chei_js().items()):
        chei.setdefault(k, unde)
    return chei, greseli


def verifica(dic, chei, nume=""):
    """(erori, avertismente) pentru un dictionar {cheie: text | {forma: text}}."""
    erori, avert = [], []
    dic = {k: v for k, v in dic.items() if k != "_meta"}
    for k in chei:
        if k not in dic:
            erori.append(f"{nume}: lipsește „{k}” ({chei[k]})")
    for k, v in dic.items():
        if k not in chei:
            avert.append(f"{nume}: nefolosită „{k}”")
        forme = v.values() if isinstance(v, dict) else [v]
        for f in forme:
            if not isinstance(f, str) or not f.strip():
                erori.append(f"{nume}: „{k}” are o traducere goală")
                continue
            if set(_PARAM.findall(k)) != set(_PARAM.findall(f)):
                erori.append(f"{nume}: „{k}” → „{f}”: parametrii {{...}} nu se potrivesc")
    return erori, avert


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dict", help="un singur fisier JSON de verificat")
    ap.add_argument("--chei", action="store_true", help="tipareste doar cheile")
    ap.add_argument("--avertismente", action="store_true", help="arata si cheile nefolosite")
    a = ap.parse_args()
    chei, greseli = toate_cheile()
    if a.chei:
        for k in chei:
            print(k)
        return 0
    fisiere = [a.dict] if a.dict else [os.path.join(i18n.DIR_APP, fn) for fn in sorted(os.listdir(i18n.DIR_APP))
                                       if fn.endswith(".json") and fn != "ro.json"]
    erori, avert = list(greseli), []
    for cale in fisiere:
        with open(cale, encoding="utf-8") as f:
            dic = json.load(f)
        e, w = verifica(dic, chei, os.path.basename(cale))
        erori += e
        avert += w
        print(f"{os.path.basename(cale)}: {len(dic) - ('_meta' in dic)} intrări, {len(chei)} chei în cod, "
              f"{len(e)} erori, {len(w)} nefolosite")
    for x in erori:
        print("EROARE", x)
    if a.avertismente:
        for x in avert:
            print("AVERT ", x)
    return 1 if erori else 0


if __name__ == "__main__":
    sys.exit(main())
